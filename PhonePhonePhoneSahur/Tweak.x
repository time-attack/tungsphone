// PhonePhonePhoneSahur — animated Tung Tung Tung Sahur on the SpringBoard.
//
// Built with Xcode 16.4 (clang-1700 / SDK 18.5) — the toolchain whose arm64e iOS
// 15.1.1 can authenticate. Entry is an ObjC +load (runtime-signed), NOT a C
// constructor. UIKit/QuartzCore/ImageIO only — NO AVFoundation/audio in
// SpringBoard (audio session activation from SpringBoard deadlocks; audio is
// handled separately). Every entry point is wrapped so it can never crash SB.
//
// Behaviour:
//   * floats a small draggable pass-through window with the Sahur sprite (GIF).
//   * the laptop agent writes the next tap target to sahur_tap.txt ("x y seq");
//     Sahur walks there and pokes it (ripple) just before the real tap lands.
//   * tap the sprite -> Darwin "com.sahur.toggle" (start/stop a voice turn).
//   * Darwin "com.sahur.state.*" drives a speech-bubble caption.

#import <UIKit/UIKit.h>
#import <QuartzCore/QuartzCore.h>
#import <ImageIO/ImageIO.h>
#import <notify.h>

#define SAHUR_SAFE(...) do { @try { __VA_ARGS__ } @catch (__unused NSException *e) {} } while (0)
#define TOGGLE_NOTIFY "com.sahur.toggle"
#define TAP_FILE @"/var/mobile/Library/Caches/sahur_tap.txt"
#define PERSONA_FILE @"/var/mobile/Library/Caches/sahur_persona.txt"

static const CGFloat kWinW = 150, kWinH = 175, kChar = 120;
static BOOL gAutomating = NO;

// Switchable personalities. Long-press the sprite to cycle; the active name is
// written to PERSONA_FILE so the laptop brain picks the matching voice + music.
static NSArray<NSDictionary *> *PersonaList(void) {
    return @[@{@"name":@"sahur",   @"png":@"sahur.png",   @"label":@"Tung Tung Sahur"},
             @{@"name":@"bibi",    @"png":@"bibi.png",    @"label":@"Bibi Netanyahu"},
             @{@"name":@"trump",   @"png":@"trump.png",   @"label":@"Donald Trump"},
             @{@"name":@"charlie", @"png":@"charlie.png", @"label":@"Charlie Kirk"},
             @{@"name":@"obama",   @"png":@"obama.png",   @"label":@"Barack Obama"},
             @{@"name":@"biden",   @"png":@"biden.png",   @"label":@"Joe Biden"},
             @{@"name":@"mrbeast", @"png":@"mrbeast.png", @"label":@"MrBeast"}];
}
static NSInteger PersonaIndexForName(NSString *name) {
    NSArray *l = PersonaList();
    for (NSInteger i = 0; i < (NSInteger)l.count; i++)
        if ([l[i][@"name"] isEqualToString:name]) return i;
    return 0;
}

// ---- assets ----------------------------------------------------------------

static NSString *AssetPath(NSString *name) {
    for (NSString *b in @[@"/var/jb/Library/Application Support/PhonePhonePhoneSahur/",
                          @"/Library/Application Support/PhonePhonePhoneSahur/"]) {
        NSString *p = [b stringByAppendingString:name];
        if ([[NSFileManager defaultManager] fileExistsAtPath:p]) return p;
    }
    return nil;
}

static NSArray<UIImage *> *LoadGIF(NSString *name, NSTimeInterval *outDur) {
    NSString *path = AssetPath(name);
    NSData *data = path ? [NSData dataWithContentsOfFile:path] : nil;
    if (!data) return nil;
    CGImageSourceRef src = CGImageSourceCreateWithData((__bridge CFDataRef)data, NULL);
    if (!src) return nil;
    size_t n = CGImageSourceGetCount(src);
    NSMutableArray *frames = [NSMutableArray array];
    NSTimeInterval total = 0;
    for (size_t i = 0; i < n; i++) {
        CGImageRef cg = CGImageSourceCreateImageAtIndex(src, i, NULL);
        if (cg) { [frames addObject:[UIImage imageWithCGImage:cg]]; CGImageRelease(cg); }
        NSTimeInterval d = 0.1;
        CFDictionaryRef pr = CGImageSourceCopyPropertiesAtIndex(src, i, NULL);
        if (pr) {
            NSDictionary *p = (__bridge NSDictionary *)pr;
            NSDictionary *g = p[(__bridge NSString *)kCGImagePropertyGIFDictionary];
            NSNumber *ut = g[(__bridge NSString *)kCGImagePropertyGIFUnclampedDelayTime];
            NSNumber *dt = g[(__bridge NSString *)kCGImagePropertyGIFDelayTime];
            if (ut && ut.doubleValue > 0) d = ut.doubleValue; else if (dt && dt.doubleValue > 0) d = dt.doubleValue;
            CFRelease(pr);
        }
        total += d;
    }
    CFRelease(src);
    if (outDur) *outDur = total > 0 ? total : n * 0.1;
    return frames.count ? frames : nil;
}

// ---- pass-through window ----------------------------------------------------

@interface SahurWindow : UIWindow
@end
@implementation SahurWindow
- (UIView *)hitTest:(CGPoint)point withEvent:(UIEvent *)event {
    if (gAutomating) return nil;
    UIView *v = nil;
    @try { v = [super hitTest:point withEvent:event]; } @catch (__unused NSException *e) { return nil; }
    return (v == self) ? nil : v;
}
@end

// ---- controller ------------------------------------------------------------

@interface SahurController : NSObject
@property (nonatomic, strong) SahurWindow *window;
@property (nonatomic, strong) UIImageView *character;
@property (nonatomic, strong) UIView *bubble;
@property (nonatomic, strong) UILabel *bubbleLabel;
@property (nonatomic, strong) NSTimer *pollTimer;
@property (nonatomic, strong) NSTimer *returnTimer;
@property (nonatomic, strong) NSTimer *watchdog;
@property (nonatomic, copy)   NSString *lastTap;
@property (nonatomic, assign) CGPoint idleOrigin;
@property (nonatomic, assign) int tries;
@property (nonatomic, assign) BOOL registered;
@property (nonatomic, assign) CGFloat facing;
@property (nonatomic, assign) NSInteger personaIdx;
@property (nonatomic, strong) UIWindow *picker;
@property (nonatomic, strong) NSArray<UIImage *> *idleF, *walkF, *attackF;
@property (nonatomic, assign) NSTimeInterval idleD, walkD, attackD;
+ (instancetype)shared;
@end

static void sahur_state_cb(CFNotificationCenterRef c, void *o, CFStringRef name, const void *ob, CFDictionaryRef ui);

@implementation SahurController

+ (instancetype)shared {
    static SahurController *s; static dispatch_once_t once;
    dispatch_once(&once, ^{ s = [SahurController new]; });
    return s;
}

// Entry — runs via the ObjC runtime (runtime-signed), not a C constructor.
+ (void)load {
    SAHUR_SAFE({
        SahurController *c = [self shared];
        c.facing = 1.0;
        [[NSNotificationCenter defaultCenter] addObserver:c selector:@selector(appActive:)
            name:UIApplicationDidBecomeActiveNotification object:nil];
        CFNotificationCenterRef nc = CFNotificationCenterGetDarwinNotifyCenter();
        const char *names[] = {"com.sahur.state.idle","com.sahur.state.listening",
                               "com.sahur.state.thinking","com.sahur.state.speaking"};
        for (int i = 0; i < 4; i++)
            CFNotificationCenterAddObserver(nc, (__bridge const void *)c, sahur_state_cb,
                (__bridge CFStringRef)[NSString stringWithUTF8String:names[i]], NULL,
                CFNotificationSuspensionBehaviorDeliverImmediately);
        dispatch_async(dispatch_get_main_queue(), ^{ SAHUR_SAFE({ [c startWatchdog]; }); });
    });
}

- (void)appActive:(NSNotification *)n { SAHUR_SAFE({ [self startWatchdog]; [self scheduleInstall]; }); }

// Self-heal: every 2s, if our overlay died (window gone, or its scene was torn down
// by a respring / app transition leaving it orphaned), rebuild it on the live scene.
// This is why Sahur "disappears" — the scene gets deallocated and the old window is
// never recreated. The watchdog brings him back automatically.
- (void)startWatchdog {
    if (self.watchdog) return;
    self.watchdog = [NSTimer scheduledTimerWithTimeInterval:2.0 target:self
        selector:@selector(healthCheck) userInfo:nil repeats:YES];
}
- (void)healthCheck {
    SAHUR_SAFE({
        if (!self.window || !self.window.windowScene) {   // orphaned / dead -> rebuild
            self.window = nil; self.tries = 0; [self install];
        } else if (self.window.hidden) {
            self.window.hidden = NO;
        }
    });
}

- (UIWindowScene *)scene {
    if (@available(iOS 13.0, *))
        for (UIScene *s in [UIApplication sharedApplication].connectedScenes)
            if ([s isKindOfClass:[UIWindowScene class]] && s.activationState == UISceneActivationStateForegroundActive)
                return (UIWindowScene *)s;
    return nil;
}

- (void)scheduleInstall {
    if (self.window || self.tries > 25) return;
    self.tries++;
    SAHUR_SAFE({ [self install]; });
    if (!self.window)
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.5 * NSEC_PER_SEC)),
                       dispatch_get_main_queue(), ^{ [self scheduleInstall]; });
}

- (void)install {
    if (self.window) return;
    [self.pollTimer invalidate]; self.pollTimer = nil;   // avoid dup timers on rebuild
    SAHUR_SAFE({
        UIWindowScene *sc = [self scene];
        if (!sc) return;
        CGRect scr = [UIScreen mainScreen].bounds;
        self.idleOrigin = CGPointMake(scr.size.width - kWinW + 28, scr.size.height - kWinH - 90);

        SahurWindow *w = [[SahurWindow alloc] initWithWindowScene:sc];
        w.frame = CGRectMake(self.idleOrigin.x, self.idleOrigin.y, kWinW, kWinH);
        w.windowLevel = UIWindowLevelAlert + 1000;
        w.backgroundColor = [UIColor clearColor];
        UIViewController *vc = [UIViewController new];
        vc.view.backgroundColor = [UIColor clearColor];
        w.rootViewController = vc;

        UIImageView *iv = [[UIImageView alloc] initWithFrame:CGRectMake((kWinW-kChar)/2, kWinH-kChar, kChar, kChar)];
        iv.contentMode = UIViewContentModeScaleAspectFit;
        iv.userInteractionEnabled = YES;
        iv.layer.shadowColor = [UIColor blackColor].CGColor; iv.layer.shadowOpacity = 0.4;
        iv.layer.shadowRadius = 8; iv.layer.shadowOffset = CGSizeMake(0, 4);
        iv.image = [UIImage imageWithContentsOfFile:AssetPath(@"sahur.png") ?: @""];   // static, no animation
        if (!iv.image) { UILabel *g=[[UILabel alloc] initWithFrame:iv.bounds]; g.text=@"\U0001FAB5";
            g.font=[UIFont systemFontOfSize:64]; g.textAlignment=NSTextAlignmentCenter; [iv addSubview:g]; }
        [vc.view addSubview:iv];

        UIView *bub = [[UIView alloc] initWithFrame:CGRectMake(4, 2, kWinW-8, 44)];
        bub.backgroundColor = [UIColor colorWithWhite:0.08 alpha:0.92];
        bub.layer.cornerRadius = 14; bub.layer.borderWidth = 2;
        bub.layer.borderColor = [UIColor colorWithRed:1 green:0.78 blue:0.2 alpha:1].CGColor;
        bub.userInteractionEnabled = NO; bub.alpha = 0;
        UILabel *bl = [[UILabel alloc] initWithFrame:CGRectInset(bub.bounds, 10, 4)];
        bl.numberOfLines = 2; bl.textColor = [UIColor whiteColor]; bl.textAlignment = NSTextAlignmentCenter;
        bl.font = [UIFont systemFontOfSize:13 weight:UIFontWeightSemibold];
        [bub addSubview:bl]; [vc.view addSubview:bub];

        [iv addGestureRecognizer:[[UITapGestureRecognizer alloc] initWithTarget:self action:@selector(onTap:)]];
        [iv addGestureRecognizer:[[UIPanGestureRecognizer alloc] initWithTarget:self action:@selector(onPan:)]];
        UILongPressGestureRecognizer *lp = [[UILongPressGestureRecognizer alloc] initWithTarget:self action:@selector(onLongPress:)];
        lp.minimumPressDuration = 0.55;
        [iv addGestureRecognizer:lp];

        w.hidden = NO;
        self.window = w; self.character = iv; self.bubble = bub; self.bubbleLabel = bl;
        // restore the last-chosen persona (persisted across resprings)
        NSString *saved = [NSString stringWithContentsOfFile:PERSONA_FILE encoding:NSUTF8StringEncoding error:nil];
        [self applyPersona:PersonaIndexForName([(saved ?: @"sahur") stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]]) persist:NO announce:NO];
        self.lastTap = [self readTap];
        self.pollTimer = [NSTimer scheduledTimerWithTimeInterval:0.08 target:self selector:@selector(pollTaps) userInfo:nil repeats:YES];
    });
}

// ---- animation ----
- (void)playFrames:(NSArray *)f dur:(NSTimeInterval)d rep:(NSInteger)r {
    if (!f.count || !self.character) return;
    self.character.animationImages = f;
    self.character.animationDuration = d > 0 ? d : 0.5;
    self.character.animationRepeatCount = r;
    [self.character startAnimating];
}
- (void)playIdle { [self playFrames:self.idleF dur:self.idleD rep:0]; }
- (void)face:(CGFloat)dir { self.facing = dir; self.character.transform = CGAffineTransformMakeScale(dir, 1.0); }

// ---- tap-target channel ----
- (NSString *)readTap {
    NSString *s = nil; SAHUR_SAFE({ s = [NSString stringWithContentsOfFile:TAP_FILE encoding:NSUTF8StringEncoding error:nil]; });
    return s ?: @"";
}
- (void)pollTaps {
    SAHUR_SAFE({
        NSString *cur = [self readTap];
        if (!cur.length || [cur isEqualToString:self.lastTap]) return;
        self.lastTap = cur;
        NSMutableArray *nm = [NSMutableArray array];
        for (NSString *p in [cur componentsSeparatedByCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]]) if (p.length) [nm addObject:p];
        if (nm.count < 2) return;
        CGFloat x = [nm[0] floatValue], y = [nm[1] floatValue];
        if (x <= 0 && y <= 0) return;
        [self walkToX:x y:y];
    });
}
- (void)walkToX:(CGFloat)x y:(CGFloat)y {
    SAHUR_SAFE({
        [self.returnTimer invalidate]; self.returnTimer = nil;
        gAutomating = YES; [self showBubble:@"tung tung tung…"];
        CGRect screen = [UIScreen mainScreen].bounds;
        // Stand BESIDE the target so the window never covers (x,y) — otherwise the
        // injected tap hits Sahur's window instead of the app. side: -1 = left of target.
        CGFloat side = (x > screen.size.width / 2.0) ? -1.0 : 1.0;
        CGFloat gap = 14;
        CGFloat ox = (side < 0) ? (x - gap - kWinW) : (x + gap);
        CGFloat oy = y - kWinH / 2.0;
        oy = MAX(8, MIN(oy, screen.size.height - kWinH - 8));
        ox = MAX(2, MIN(ox, screen.size.width - kWinW - 2));
        CGPoint from = self.window.frame.origin;
        CGPoint to = CGPointMake(ox, oy);
        NSTimeInterval dur = MIN(0.7, MAX(0.25, hypot(to.x-from.x, to.y-from.y)/900.0));
        [UIView animateWithDuration:dur delay:0 options:UIViewAnimationOptionCurveEaseInOut
            animations:^{ CGRect f=self.window.frame; f.origin=to; self.window.frame=f; }
            completion:^(BOOL fin){ SAHUR_SAFE({ [self poke]; }); }];
    });
}
- (void)poke {
    [self showBubble:@"tap! 🪵"];
    self.returnTimer = [NSTimer scheduledTimerWithTimeInterval:2.5 target:self selector:@selector(returnToIdle) userInfo:nil repeats:NO];
}
- (void)returnToIdle {
    SAHUR_SAFE({
        [self showBubble:nil];
        [UIView animateWithDuration:0.5 delay:0 options:UIViewAnimationOptionCurveEaseInOut
            animations:^{ CGRect f=self.window.frame; f.origin=self.idleOrigin; self.window.frame=f; }
            completion:^(BOOL fin){ gAutomating=NO; }];
    });
}

// ---- personality switching ----
- (void)applyPersona:(NSInteger)idx persist:(BOOL)persist announce:(BOOL)announce {
    SAHUR_SAFE({
        NSArray *list = PersonaList();
        if (!list.count) return;
        idx = ((idx % (NSInteger)list.count) + (NSInteger)list.count) % (NSInteger)list.count;
        self.personaIdx = idx;
        NSDictionary *d = list[idx];
        UIImage *img = [UIImage imageWithContentsOfFile:AssetPath(d[@"png"]) ?: @""];
        if (img) self.character.image = img;
        if (announce) {
            [self showBubble:[NSString stringWithFormat:@"now: %@", d[@"label"]]];
            [self.returnTimer invalidate];
            self.returnTimer = [NSTimer scheduledTimerWithTimeInterval:1.6 target:self
                selector:@selector(hideBubbleTick) userInfo:nil repeats:NO];
        }
        if (persist)
            [d[@"name"] writeToFile:PERSONA_FILE atomically:YES encoding:NSUTF8StringEncoding error:nil];
    });
}
- (void)hideBubbleTick { SAHUR_SAFE({ [self showBubble:nil]; }); }
- (void)onLongPress:(UILongPressGestureRecognizer *)g {
    if (g.state != UIGestureRecognizerStateBegan) return;   // fire once per press
    SAHUR_SAFE({ [self showPicker]; });                     // open the tap-to-pick menu
}

// ---- persona picker menu (long-press the sprite) ----
- (void)showPicker {
    if (self.picker) { [self dismissPicker]; return; }
    UIWindowScene *sc = [self scene];
    if (!sc) return;
    CGRect scr = [UIScreen mainScreen].bounds;
    NSArray *list = PersonaList();

    UIWindow *w = [[UIWindow alloc] initWithWindowScene:sc];
    w.frame = scr;
    w.windowLevel = UIWindowLevelAlert + 2000;     // above the sprite
    w.backgroundColor = [UIColor clearColor];
    UIViewController *vc = [UIViewController new];
    vc.view.backgroundColor = [UIColor clearColor];
    w.rootViewController = vc;

    // dim backdrop: tap anywhere outside the card to dismiss
    UIView *dim = [[UIView alloc] initWithFrame:scr];
    dim.backgroundColor = [UIColor colorWithWhite:0 alpha:0.5];
    [dim addGestureRecognizer:[[UITapGestureRecognizer alloc] initWithTarget:self action:@selector(dismissPicker)]];
    [vc.view addSubview:dim];

    const CGFloat cardW = 280, rowH = 60, gap = 10, titleH = 46, pad = 16;
    CGFloat bodyH = titleH + (CGFloat)list.count * (rowH + gap) + pad;
    CGFloat cardX = (scr.size.width - cardW) / 2.0;
    CGFloat cardY = (scr.size.height - bodyH) / 2.0;

    UIView *card = [[UIView alloc] initWithFrame:CGRectMake(cardX, cardY, cardW, bodyH)];
    card.backgroundColor = [UIColor colorWithWhite:0.10 alpha:0.97];
    card.layer.cornerRadius = 24;
    card.layer.borderWidth = 2;
    card.layer.borderColor = [UIColor colorWithRed:1 green:0.78 blue:0.2 alpha:1].CGColor;
    [vc.view addSubview:card];

    UILabel *title = [[UILabel alloc] initWithFrame:CGRectMake(0, 6, cardW, titleH)];
    title.text = @"who you want? 🪵";
    title.textColor = [UIColor whiteColor];
    title.textAlignment = NSTextAlignmentCenter;
    title.font = [UIFont systemFontOfSize:19 weight:UIFontWeightBold];
    [card addSubview:title];

    for (NSInteger i = 0; i < (NSInteger)list.count; i++) {
        NSDictionary *d = list[i];
        CGFloat y = titleH + i * (rowH + gap);
        UIButton *b = [UIButton buttonWithType:UIButtonTypeCustom];
        b.frame = CGRectMake(12, y, cardW - 24, rowH);
        b.tag = i;
        BOOL cur = (i == self.personaIdx);
        b.backgroundColor = cur ? [UIColor colorWithRed:1 green:0.78 blue:0.2 alpha:0.30]
                                : [UIColor colorWithWhite:1 alpha:0.07];
        b.layer.cornerRadius = 14;
        [b addTarget:self action:@selector(pickPersona:) forControlEvents:UIControlEventTouchUpInside];

        UIImageView *iv = [[UIImageView alloc] initWithFrame:CGRectMake(8, 6, rowH - 12, rowH - 12)];
        iv.contentMode = UIViewContentModeScaleAspectFit;
        iv.image = [UIImage imageWithContentsOfFile:AssetPath(d[@"png"]) ?: @""];
        iv.userInteractionEnabled = NO;
        [b addSubview:iv];

        UILabel *lb = [[UILabel alloc] initWithFrame:CGRectMake(rowH + 6, 0, cardW - 24 - rowH - 14, rowH)];
        lb.text = d[@"label"];
        lb.textColor = [UIColor whiteColor];
        lb.font = [UIFont systemFontOfSize:17 weight:UIFontWeightSemibold];
        lb.userInteractionEnabled = NO;
        [b addSubview:lb];

        [card addSubview:b];
    }

    w.hidden = NO;
    self.picker = w;
}
- (void)pickPersona:(UIButton *)b {
    SAHUR_SAFE({
        [self applyPersona:b.tag persist:YES announce:YES];
        [self dismissPicker];
    });
}
- (void)dismissPicker {
    SAHUR_SAFE({
        self.picker.hidden = YES;
        self.picker = nil;
    });
}

// ---- gestures + bubble ----
- (void)onTap:(UITapGestureRecognizer *)g { SAHUR_SAFE({ notify_post(TOGGLE_NOTIFY); [self showBubble:@"listening…"]; }); }
- (void)onPan:(UIPanGestureRecognizer *)g {
    SAHUR_SAFE({
        CGPoint t=[g translationInView:self.window]; CGRect f=self.window.frame;
        f.origin.x+=t.x; f.origin.y+=t.y; self.window.frame=f; [g setTranslation:CGPointZero inView:self.window];
        if (g.state==UIGestureRecognizerStateEnded) self.idleOrigin=f.origin;
    });
}
- (void)showBubble:(NSString *)t {
    if (!t.length) { [UIView animateWithDuration:0.2 animations:^{ self.bubble.alpha=0; }]; return; }
    self.bubbleLabel.text = t; [UIView animateWithDuration:0.2 animations:^{ self.bubble.alpha=1; }];
}
- (void)voiceState:(NSString *)s {
    NSString *say = [s isEqualToString:@"listening"] ? @"listening…" :
                    [s isEqualToString:@"thinking"] ? @"tung tung tung…" :
                    [s isEqualToString:@"speaking"] ? @"SAHUR! 🪵" : nil;
    [self showBubble:say];
}
@end

static void sahur_state_cb(CFNotificationCenterRef c, void *o, CFStringRef name, const void *ob, CFDictionaryRef ui) {
    NSString *n = (__bridge NSString *)name;
    dispatch_async(dispatch_get_main_queue(), ^{
        SAHUR_SAFE({
            NSString *s = [n lastPathComponent]; // not a path, but grabs trailing token after last '.'
            if ([n hasSuffix:@"listening"]) s=@"listening"; else if ([n hasSuffix:@"thinking"]) s=@"thinking";
            else if ([n hasSuffix:@"speaking"]) s=@"speaking"; else s=@"idle";
            [[SahurController shared] voiceState:s];
        });
    });
}
