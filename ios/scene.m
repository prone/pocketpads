
// ---- PocketPads: scene lifecycle for SDL2 (added to the template's main.m by build.sh) ----
// iOS 27 stops apps that do not use the scene lifecycle, and SDL2 predates it. This gives the app a
// scene, hands every window SDL makes to that scene, and passes the scene's events on to SDL's delegate.
#import <objc/runtime.h>

static UIWindowScene *pp_scene = nil;
static NSHashTable *pp_windows = nil;

@interface UIWindow (PPScene)
@end
@implementation UIWindow (PPScene)
+ (void)load {
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        pp_windows = [NSHashTable weakObjectsHashTable];
        Method a = class_getInstanceMethod(self, @selector(initWithFrame:));
        Method b = class_getInstanceMethod(self, @selector(pp_initWithFrame:));
        method_exchangeImplementations(a, b);
    });
}
- (instancetype)pp_initWithFrame:(CGRect)frame {
    UIWindow *w = [self pp_initWithFrame:frame];
    if (w && w.windowScene == nil) {
        if (pp_scene) w.windowScene = pp_scene;
        else [pp_windows addObject:w];
    }
    return w;
}
@end

@interface PPSceneDelegate : UIResponder <UIWindowSceneDelegate>
@end
@implementation PPSceneDelegate
- (void)scene:(UIScene *)scene willConnectToSession:(UISceneSession *)session options:(UISceneConnectionOptions *)options {
    if (![scene isKindOfClass:[UIWindowScene class]]) return;
    pp_scene = (UIWindowScene *)scene;
    for (UIWindow *w in pp_windows.allObjects) {
        BOOL shown = !w.hidden;
        w.windowScene = pp_scene;
        if (shown) [w makeKeyAndVisible];
    }
    [pp_windows removeAllObjects];
}
- (void)pp_forward:(SEL)sel {
    UIApplication *app = [UIApplication sharedApplication];
    id d = app.delegate;
    if ([d respondsToSelector:sel]) {
        #pragma clang diagnostic push
        #pragma clang diagnostic ignored "-Warc-performSelector-leaks"
        [d performSelector:sel withObject:app];
        #pragma clang diagnostic pop
    }
}
- (void)sceneDidBecomeActive:(UIScene *)scene { [self pp_forward:@selector(applicationDidBecomeActive:)]; }
- (void)sceneWillResignActive:(UIScene *)scene { [self pp_forward:@selector(applicationWillResignActive:)]; }
- (void)sceneDidEnterBackground:(UIScene *)scene { [self pp_forward:@selector(applicationDidEnterBackground:)]; }
- (void)sceneWillEnterForeground:(UIScene *)scene { [self pp_forward:@selector(applicationWillEnterForeground:)]; }
@end
