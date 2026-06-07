#import "IOSMCPRootListController.h"
#import <Preferences/PSSpecifier.h>
#import <UIKit/UIKit.h>
#import <spawn.h>
#include <roothide.h>
#import "../IOSMCPPreferences.h"

@interface IOSMCPRootListController ()

@property (nonatomic, assign) BOOL serverRunning;

@end

@implementation IOSMCPRootListController

- (NSArray *)specifiers {
    if (!_specifiers) {
        _specifiers = [self loadSpecifiersFromPlistName:@"Root" target:self];
    }

    return _specifiers;
}

- (void)viewDidLoad {
    [super viewDidLoad];
    self.navigationItem.rightBarButtonItem = [[UIBarButtonItem alloc] initWithTitle:@"Respring"
                                                                              style:UIBarButtonItemStylePlain
                                                                             target:self
                                                                             action:@selector(respringDevice:)];
}

- (void)viewWillAppear:(BOOL)animated {
    [super viewWillAppear:animated];
    [self refreshPromptText];
    [self refreshServerStatus];
}

- (void)toggleServer:(PSSpecifier *)specifier {
    BOOL shouldStart = !self.serverRunning;
    [self updateEnabledPreference:shouldStart];
    [self postNotification:shouldStart ? IOS_MCP_DARWIN_NOTIFICATION_START : IOS_MCP_DARWIN_NOTIFICATION_STOP];
    [self updateControlStatusText:shouldStart ? @"Current status: starting..." : @"Current status: stopping..."
                      buttonTitle:shouldStart ? @"Starting..." : @"Stopping..."
                    buttonEnabled:NO];

    [self showAlertWithTitle:shouldStart ? @"iOS MCP Started" : @"iOS MCP Stopped"
                     message:shouldStart ? @"The service has started and will start automatically the next time SpringBoard launches."
                                        : @"The service has stopped and will stay off until you start it manually again."];

    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(800 * NSEC_PER_MSEC)),
                   dispatch_get_main_queue(), ^{
        [self refreshServerStatus];
    });
}

- (void)copyPrompt:(PSSpecifier *)specifier {
    [UIPasteboard generalPasteboard].string = [self codexPrompt];
    [self showAlertWithTitle:@"Copied"
                     message:@"The MCP prompt snippet has been copied to the clipboard — just paste it into your prompt."];
}

- (void)respringDevice:(PSSpecifier *)specifier {
    UIAlertController *alert = [UIAlertController alertControllerWithTitle:@"Respring SpringBoard"
                                                                  message:@"Are you sure you want to respring SpringBoard? You will need to unlock the device again afterward."
                                                           preferredStyle:UIAlertControllerStyleAlert];
    [alert addAction:[UIAlertAction actionWithTitle:@"Cancel" style:UIAlertActionStyleCancel handler:nil]];
    [alert addAction:[UIAlertAction actionWithTitle:@"Respring" style:UIAlertActionStyleDestructive handler:^(UIAlertAction *action) {
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(300 * NSEC_PER_MSEC)), dispatch_get_main_queue(), ^{
            pid_t pid;
            const char *argv[] = {"killall", "SpringBoard", NULL};
            NSString *killallPath = jbroot(@"/usr/bin/killall");
            const char *spawnPath = killallPath.length ? killallPath.fileSystemRepresentation : "/usr/bin/killall";
            posix_spawn(&pid, spawnPath, NULL, NULL, (char *const *)argv, NULL);
        });
    }]];
    [self presentViewController:alert animated:YES completion:nil];
}

- (void)refreshServerStatus {
    [self updateControlStatusText:@"Current status: checking..."
                      buttonTitle:@"Checking..."
                    buttonEnabled:NO];

    NSURL *url = [NSURL URLWithString:[NSString stringWithFormat:@"http://127.0.0.1:%d/health", IOS_MCP_DEFAULT_PORT]];
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:url];
    request.timeoutInterval = 1.0;
    request.cachePolicy = NSURLRequestReloadIgnoringLocalCacheData;

    NSURLSessionConfiguration *configuration = [NSURLSessionConfiguration ephemeralSessionConfiguration];
    configuration.timeoutIntervalForRequest = 1.0;
    configuration.timeoutIntervalForResource = 1.0;

    __weak typeof(self) weakSelf = self;
    NSURLSession *session = [NSURLSession sessionWithConfiguration:configuration];
    NSURLSessionDataTask *task = [session dataTaskWithRequest:request
                                            completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        __strong typeof(weakSelf) self = weakSelf;
        if (!self) {
            [session finishTasksAndInvalidate];
            return;
        }

        BOOL running = [self isHealthyServerResponseData:data response:response error:error];
        dispatch_async(dispatch_get_main_queue(), ^{
            self.serverRunning = running;
            [self updateControlStatusText:running ? @"Current status: running" : @"Current status: not running"
                              buttonTitle:running ? @"Stop iOS MCP" : @"Start iOS MCP"
                            buttonEnabled:YES];
        });

        [session finishTasksAndInvalidate];
    }];
    [task resume];
}

- (BOOL)isHealthyServerResponseData:(NSData *)data response:(NSURLResponse *)response error:(NSError *)error {
    if (error || !data) {
        return NO;
    }

    NSHTTPURLResponse *httpResponse = [response isKindOfClass:[NSHTTPURLResponse class]] ? (NSHTTPURLResponse *)response : nil;
    if (httpResponse.statusCode != 200) {
        return NO;
    }

    NSError *jsonError = nil;
    NSDictionary *payload = [NSJSONSerialization JSONObjectWithData:data options:0 error:&jsonError];
    if (jsonError) {
        return NO;
    }

    if (![payload isKindOfClass:[NSDictionary class]]) {
        return NO;
    }

    NSString *status = [payload[@"status"] isKindOfClass:[NSString class]] ? payload[@"status"] : nil;
    NSString *server = [payload[@"server"] isKindOfClass:[NSString class]] ? payload[@"server"] : nil;
    return [status isEqualToString:@"ok"] && [server isEqualToString:@"ios-mcp"];
}

- (void)refreshPromptText {
    PSSpecifier *promptSpecifier = [self specifierForID:@"codexPromptGroup"];
    if (!promptSpecifier) {
        return;
    }

    [promptSpecifier setProperty:[self codexPrompt] forKey:PSFooterTextGroupKey];
    [self reloadSpecifier:promptSpecifier animated:NO];
}

- (void)updateControlStatusText:(NSString *)statusText buttonTitle:(NSString *)buttonTitle buttonEnabled:(BOOL)buttonEnabled {
    PSSpecifier *groupSpecifier = [self specifierForID:@"serviceControlGroup"];
    PSSpecifier *toggleSpecifier = [self specifierForID:@"toggleServerButton"];

    if (groupSpecifier) {
        [groupSpecifier setProperty:statusText forKey:PSFooterTextGroupKey];
        [self reloadSpecifier:groupSpecifier animated:NO];
    }

    if (toggleSpecifier) {
        toggleSpecifier.name = buttonTitle;
        [toggleSpecifier setProperty:buttonTitle forKey:PSTitleKey];
        [toggleSpecifier setProperty:@(buttonEnabled) forKey:PSEnabledKey];
        [self reloadSpecifier:toggleSpecifier animated:NO];
    }
}

- (void)updateEnabledPreference:(BOOL)enabled {
    CFPreferencesSetAppValue((__bridge CFStringRef)IOS_MCP_ENABLED_PREFERENCE_KEY,
                             enabled ? kCFBooleanTrue : kCFBooleanFalse,
                             (__bridge CFStringRef)IOS_MCP_PREFERENCES_DOMAIN);
    CFPreferencesAppSynchronize((__bridge CFStringRef)IOS_MCP_PREFERENCES_DOMAIN);
}

- (void)postNotification:(CFStringRef)notificationName {
    CFNotificationCenterPostNotification(CFNotificationCenterGetDarwinNotifyCenter(),
                                         notificationName,
                                         NULL,
                                         NULL,
                                         YES);
}

- (NSString *)codexPrompt {
    return [NSString stringWithFormat:
            @"You can operate an iPhone device through the iOS MCP service.\n\n"
            @"MCP address: %@\n\n"
            @"Supported operations:\n"
            @"- Touch: tap, swipe, long press, double tap, drag\n"
            @"- Text input: fast paste input, character-by-character keyboard simulation, special keys (enter, delete, etc.)\n"
            @"- Hardware buttons: Home, Power, Volume, Mute\n"
            @"- Wake / return to Home: wake_and_home (use first when locked or screen off)\n"
            @"- Screenshot (screenshot returns MCP image content, not text; the base64 image is in result.content[0].data, mimeType is usually image/jpeg)\n"
            @"- App management: launch, kill, list, install IPA (no signing required), uninstall\n"
            @"- UI accessibility: get the current page node tree, look up elements by coordinates\n"
            @"- Clipboard: read/write clipboard contents\n"
            @"- Device control: brightness, volume\n"
            @"- Open URL or URL scheme\n"
            @"- Shell command execution\n"
            @"- Device info: model, iOS version, battery, storage, system info\n\n"
            @"Operating rules:\n"
            @"1. Before starting, get the current frontmost app, screen info, UI nodes, and any needed screenshots.\n"
            @"2. If get_screen_info shows locked=true/screen_on=false, or the screenshot looks like the Lock Screen, do not continue normal app operations; first call wake_and_home, or press Power then Home, or press Home twice, then take a new screenshot to confirm.\n"
            @"3. The server enforces lock-screen protection; while locked or screen off, interactive/mutating tools such as tap, swipe, input, app launch, and shell are blocked — only status queries, screenshots, and recovery tools like wake_and_home are allowed.\n"
            @"4. Do not treat a single press_home as already reaching the Home screen; while locked, one Home press usually only wakes the device or shows the unlock prompt.\n"
            @"5. When interacting, prefer tapping and typing based on UI nodes — do not tap blindly.\n"
            @"6. After the page changes, re-read the UI nodes before continuing to the next step.\n"
            @"7. If the target element is not obvious, take a screenshot first, then decide.\n"
            @"8. For text input, use input_text first; if input_text fails, times out, or returns isError, immediately use type_text for the same text — do not repeatedly call input_text.\n"
            @"9. For health checks, do not use for i in {1..30}, because some /bin/sh does not expand braces. Use while/seq and set --connect-timeout 3 --max-time 5, for example: i=0; while [ $i -lt 30 ]; do r=$(curl -sS --connect-timeout 3 --max-time 5 %@ 2>/dev/null || true); [ -n \"$r\" ] && echo \"$r\" && exit 0; i=$((i+1)); sleep 1; done; echo health_timeout; exit 1\n"
            @"10. When handling screenshot results, parse them as image content — do not read result.content[0].text.",
            IOSMCPServiceURLString(),
            IOSMCPHealthURLString()];
}

- (void)showAlertWithTitle:(NSString *)title message:(NSString *)message {
    UIAlertController *alertController = [UIAlertController alertControllerWithTitle:title
                                                                             message:message
                                                                      preferredStyle:UIAlertControllerStyleAlert];
    [alertController addAction:[UIAlertAction actionWithTitle:@"OK"
                                                        style:UIAlertActionStyleDefault
                                                      handler:nil]];
    [self presentViewController:alertController animated:YES completion:nil];
}

@end
