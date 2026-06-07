# Build (rootless, device toolchain):  make package THEOS_PACKAGE_SCHEME=rootless
#
# FAT arm64 + arm64e: SpringBoard (and apps) on this A14 device are arm64e, so the
# dylib MUST carry an arm64e slice to load. device control server ships the same and loads fine.
# Frameworks: UIKit + CoreGraphics + QuartzCore ONLY (no audio/ImageIO).
ARCHS = arm64 arm64e
# Built with Xcode 16.4 (clang 16, iOS 18.5 SDK) via DEVELOPER_DIR — the exact
# toolchain generation that built the working device control server. "latest" resolves to that
# Xcode's 18.5 SDK, producing arm64e that iOS 15.1.1's dyld can authenticate.
TARGET := iphone:clang:latest:15.0

include $(THEOS)/makefiles/common.mk

TWEAK_NAME = PhonePhonePhoneSahur
PhonePhonePhoneSahur_FILES = Tweak.x
PhonePhonePhoneSahur_FRAMEWORKS = UIKit CoreGraphics QuartzCore ImageIO
PhonePhonePhoneSahur_CFLAGS = -fobjc-arc -Wno-deprecated-declarations -Wno-unused-function

include $(THEOS_MAKE_PATH)/tweak.mk
