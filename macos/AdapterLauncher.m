#import <Cocoa/Cocoa.h>
#import <signal.h>
#import <unistd.h>

@interface PasteSecureTextField : NSSecureTextField
@end

@implementation PasteSecureTextField

- (BOOL)performKeyEquivalent:(NSEvent *)event {
    NSEventModifierFlags modifiers = event.modifierFlags & NSEventModifierFlagDeviceIndependentFlagsMask;
    if ((modifiers & NSEventModifierFlagCommand) != 0 &&
        [[event.charactersIgnoringModifiers lowercaseString] isEqualToString:@"v"]) {
        NSString *value = [NSPasteboard.generalPasteboard stringForType:NSPasteboardTypeString];
        if (value != nil) {
            self.stringValue = [value stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
            return YES;
        }
    }
    return [super performKeyEquivalent:event];
}

@end

@interface AppDelegate : NSObject <NSApplicationDelegate>
@property(strong) NSWindow *window;
@property(strong) NSSecureTextField *keyField;
@property(strong) NSButton *startButton;
@property(strong) NSButton *stopButton;
@property(strong) NSButton *resizeCheckbox;
@property(strong) NSTextField *statusLabel;
@property(strong) NSStackView *contentStack;
@property(strong) NSArray<NSTextField *> *budgetLabels;
@property(strong) NSMutableArray<NSTextField *> *wrappingLabels;
@property(strong) NSTimer *budgetTimer;
@property(strong) NSTask *serverTask;
@property(strong) NSURL *pidURL;
@end

@implementation AppDelegate

- (NSInteger)serverPort {
    return 4000;
}

- (NSString *)stateName {
    return [NSBundle.mainBundle.infoDictionary[@"AdapterTestCandidate"] boolValue] ? @"Codex Perplexity Adapter Test" : @"Codex Perplexity Adapter";
}

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
    [self buildApplicationMenu];
    [NSUserDefaults.standardUserDefaults registerDefaults:@{@"ResizeImagesOnLimit": @YES}];
    [self buildWindow];
    [self.window center];
    [self.window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
    [self.window makeFirstResponder:self.keyField];
}

- (void)buildApplicationMenu {
    NSMenu *mainMenu = [[NSMenu alloc] init];

    NSMenuItem *appMenuItem = [[NSMenuItem alloc] init];
    [mainMenu addItem:appMenuItem];
    NSMenu *appMenu = [[NSMenu alloc] initWithTitle:@"Codex–Perplexity Adapter"];
    [appMenu addItemWithTitle:@"About Codex–Perplexity Adapter" action:@selector(showAbout:) keyEquivalent:@""];
    [appMenu addItem:[NSMenuItem separatorItem]];
    [appMenu addItemWithTitle:@"Hide Codex–Perplexity Adapter" action:@selector(hide:) keyEquivalent:@"h"];
    [appMenu addItem:[NSMenuItem separatorItem]];
    [appMenu addItemWithTitle:@"Quit Codex–Perplexity Adapter" action:@selector(terminate:) keyEquivalent:@"q"];
    appMenuItem.submenu = appMenu;

    NSMenuItem *editMenuItem = [[NSMenuItem alloc] init];
    [mainMenu addItem:editMenuItem];
    NSMenu *editMenu = [[NSMenu alloc] initWithTitle:@"Edit"];
    [editMenu addItemWithTitle:@"Undo" action:@selector(undo:) keyEquivalent:@"z"];
    [editMenu addItem:[NSMenuItem separatorItem]];
    [editMenu addItemWithTitle:@"Cut" action:@selector(cut:) keyEquivalent:@"x"];
    [editMenu addItemWithTitle:@"Copy" action:@selector(copy:) keyEquivalent:@"c"];
    [editMenu addItemWithTitle:@"Paste" action:@selector(paste:) keyEquivalent:@"v"];
    [editMenu addItemWithTitle:@"Select All" action:@selector(selectAll:) keyEquivalent:@"a"];
    editMenuItem.submenu = editMenu;

    NSMenuItem *windowMenuItem = [[NSMenuItem alloc] init];
    [mainMenu addItem:windowMenuItem];
    NSMenu *windowMenu = [[NSMenu alloc] initWithTitle:@"Window"];
    [windowMenu addItemWithTitle:@"Minimize" action:@selector(performMiniaturize:) keyEquivalent:@"m"];
    windowMenuItem.submenu = windowMenu;
    NSApp.windowsMenu = windowMenu;

    NSApp.mainMenu = mainMenu;
}

- (void)showAbout:(id)sender {
    NSDictionary *info = NSBundle.mainBundle.infoDictionary;
    NSString *version = info[@"CFBundleShortVersionString"] ?: @"Unknown";
    NSArray<NSString *> *models = info[@"SupportedModelAliases"];

    NSAlert *alert = [[NSAlert alloc] init];
    alert.messageText = @"Codex–Perplexity Adapter";
    alert.informativeText = [NSString stringWithFormat:@"Version %@\n\nAvailable model names:\n%@\n\ngpt-5.6 is an alias for gpt-5.6-sol.",
                             version, [models componentsJoinedByString:@"\n"]];
    alert.alertStyle = NSAlertStyleInformational;
    [alert addButtonWithTitle:@"OK"];
    [alert runModal];
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender {
    return YES;
}

- (void)applicationWillTerminate:(NSNotification *)notification {
    [self stopServer];
}

- (NSTextField *)label:(NSString *)text {
    NSTextField *label = [NSTextField labelWithString:text];
    label.translatesAutoresizingMaskIntoConstraints = NO;
    return label;
}

- (void)buildWindow {
    self.wrappingLabels = [NSMutableArray array];
    self.window = [[NSWindow alloc]
        initWithContentRect:NSMakeRect(0, 0, 580, 300)
                  styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable
                    backing:NSBackingStoreBuffered
                      defer:NO];
    self.window.title = [NSBundle.mainBundle.infoDictionary[@"AdapterTestCandidate"] boolValue] ? @"Codex–Perplexity Adapter Test" : @"Codex–Perplexity Adapter";
    self.window.releasedWhenClosed = NO;

    NSView *content = self.window.contentView;
    NSTextField *title = [self label:@"Codex–Perplexity Adapter"];
    title.font = [NSFont systemFontOfSize:24 weight:NSFontWeightSemibold];

    NSTextField *description = [NSTextField wrappingLabelWithString:
        [NSString stringWithFormat:@"Runs a private local Responses API at 127.0.0.1:%ld for Codex. Your Perplexity key stays in memory and is never saved.", (long)[self serverPort]]];
    description.textColor = NSColor.secondaryLabelColor;
    [self.wrappingLabels addObject:description];

    NSTextField *keyLabel = [self label:@"Perplexity API key"];
    keyLabel.font = [NSFont systemFontOfSize:13 weight:NSFontWeightMedium];

    self.keyField = [[PasteSecureTextField alloc] init];
    self.keyField.placeholderString = @"pplx-…";
    self.keyField.editable = YES;
    self.keyField.selectable = YES;
    self.keyField.bezeled = YES;
    self.keyField.drawsBackground = YES;
    self.keyField.target = self;
    self.keyField.action = @selector(startClicked:);

    NSButton *pasteButton = [NSButton buttonWithTitle:@"Paste" target:self action:@selector(pasteKeyClicked:)];
    pasteButton.bezelStyle = NSBezelStyleRounded;
    NSStackView *keyRow = [NSStackView stackViewWithViews:@[self.keyField, pasteButton]];
    keyRow.orientation = NSUserInterfaceLayoutOrientationHorizontal;
    keyRow.spacing = 8;
    keyRow.alignment = NSLayoutAttributeCenterY;

    self.startButton = [NSButton buttonWithTitle:@"Start Adapter" target:self action:@selector(startClicked:)];
    self.startButton.bezelStyle = NSBezelStyleRounded;
    self.startButton.keyEquivalent = @"\r";

    self.stopButton = [NSButton buttonWithTitle:@"Stop" target:self action:@selector(stopClicked:)];
    self.stopButton.bezelStyle = NSBezelStyleRounded;
    self.stopButton.enabled = NO;

    self.statusLabel = [NSTextField wrappingLabelWithString:@"● Stopped"];
    self.statusLabel.textColor = NSColor.secondaryLabelColor;
    [self.wrappingLabels addObject:self.statusLabel];

    NSStackView *buttonRow = [NSStackView stackViewWithViews:@[self.startButton, self.stopButton, self.statusLabel]];
    buttonRow.orientation = NSUserInterfaceLayoutOrientationHorizontal;
    buttonRow.spacing = 10;
    buttonRow.alignment = NSLayoutAttributeCenterY;
    buttonRow.distribution = NSStackViewDistributionFill;
    [self.statusLabel setContentHuggingPriority:1 forOrientation:NSLayoutConstraintOrientationHorizontal];
    for (NSButton *button in @[self.startButton, self.stopButton]) {
        [button setContentHuggingPriority:NSLayoutPriorityRequired forOrientation:NSLayoutConstraintOrientationHorizontal];
        [button setContentCompressionResistancePriority:NSLayoutPriorityRequired forOrientation:NSLayoutConstraintOrientationHorizontal];
    }

    self.resizeCheckbox = [NSButton checkboxWithTitle:@"Resize images in oversized requests" target:self action:@selector(resizeChanged:)];
    self.resizeCheckbox.state = [NSUserDefaults.standardUserDefaults boolForKey:@"ResizeImagesOnLimit"] ? NSControlStateValueOn : NSControlStateValueOff;
    NSTextField *resizeHelp = [NSTextField wrappingLabelWithString:@"Shrinks older images to fit; small text may become harder to read."];
    resizeHelp.font = [NSFont systemFontOfSize:12];
    resizeHelp.textColor = NSColor.secondaryLabelColor;
    [self.wrappingLabels addObject:resizeHelp];
    NSMutableArray<NSTextField *> *budgetLabels = [NSMutableArray array];
    for (NSInteger row = 0; row < 5; row++) {
        NSTextField *label = [NSTextField wrappingLabelWithString:@""];
        label.font = [NSFont systemFontOfSize:12];
        label.textColor = NSColor.secondaryLabelColor;
        [budgetLabels addObject:label];
        [self.wrappingLabels addObject:label];
    }
    self.budgetLabels = budgetLabels;
    NSStackView *statistics = [NSStackView stackViewWithViews:budgetLabels];
    statistics.orientation = NSUserInterfaceLayoutOrientationVertical;
    statistics.alignment = NSLayoutAttributeLeading;
    statistics.spacing = 2;
    statistics.detachesHiddenViews = YES;
    NSStackView *stack = [NSStackView stackViewWithViews:@[title, description, keyLabel, keyRow, self.resizeCheckbox, resizeHelp, buttonRow, statistics]];
    self.contentStack = stack;
    stack.orientation = NSUserInterfaceLayoutOrientationVertical;
    stack.alignment = NSLayoutAttributeLeading;
    stack.spacing = 10;
    stack.translatesAutoresizingMaskIntoConstraints = NO;
    [content addSubview:stack];

    [NSLayoutConstraint activateConstraints:@[
        [stack.leadingAnchor constraintEqualToAnchor:content.leadingAnchor constant:20],
        [stack.trailingAnchor constraintEqualToAnchor:content.trailingAnchor constant:-20],
        [stack.topAnchor constraintEqualToAnchor:content.topAnchor constant:20],
        [keyRow.widthAnchor constraintEqualToAnchor:stack.widthAnchor],
        [buttonRow.widthAnchor constraintEqualToAnchor:stack.widthAnchor],
        [statistics.widthAnchor constraintEqualToAnchor:stack.widthAnchor],
        [self.keyField.heightAnchor constraintEqualToConstant:28],
        [description.widthAnchor constraintEqualToAnchor:stack.widthAnchor],
        [resizeHelp.widthAnchor constraintEqualToAnchor:stack.widthAnchor],
    ]];
    for (NSTextField *label in self.budgetLabels) {
        [label.widthAnchor constraintEqualToAnchor:statistics.widthAnchor].active = YES;
    }
    for (NSTextField *label in self.wrappingLabels) {
        label.maximumNumberOfLines = 0;
        label.lineBreakMode = NSLineBreakByWordWrapping;
        [label setContentCompressionResistancePriority:NSLayoutPriorityRequired forOrientation:NSLayoutConstraintOrientationVertical];
    }
    [self setBudgetRows:@[@"Activity and request statistics appear while running."]];
}

- (void)fitWindowToContent {
    [self.window.contentView layoutSubtreeIfNeeded];
    for (NSTextField *label in self.wrappingLabels) {
        label.preferredMaxLayoutWidth = NSWidth(label.frame);
        [label invalidateIntrinsicContentSize];
    }
    [self.window.contentView layoutSubtreeIfNeeded];
    CGFloat height = ceil(self.contentStack.fittingSize.height) + 40;
    NSRect frame = [self.window frameRectForContentRect:NSMakeRect(0, 0, 580, height)];
    frame.origin.x = NSMinX(self.window.frame);
    frame.origin.y = NSMaxY(self.window.frame) - NSHeight(frame);
    [self.window setFrame:frame display:YES];
}

- (void)setBudgetRows:(NSArray<NSString *> *)rows {
    for (NSUInteger index = 0; index < self.budgetLabels.count; index++) {
        self.budgetLabels[index].hidden = index >= rows.count;
        self.budgetLabels[index].stringValue = index < rows.count ? rows[index] : @"";
    }
    [self fitWindowToContent];
}

- (void)setConnectionStatus:(NSString *)text color:(NSColor *)color {
    self.statusLabel.stringValue = text;
    self.statusLabel.textColor = color;
    [self fitWindowToContent];
}

- (void)displayBudget:(NSDictionary *)values {
    NSInteger warnings = [values[@"warnings_10mb"] integerValue] + [values[@"warnings_12mb"] integerValue] + [values[@"warnings_15mb"] integerValue];
    [self setBudgetRows:@[
        [NSString stringWithFormat:@"Active: %@ · Last: %.2f MB · Largest active: %.2f MB", values[@"active_requests"], [values[@"latest_completed_request_bytes"] doubleValue] / 1000000, [values[@"largest_active_request_bytes"] doubleValue] / 1000000],
        [NSString stringWithFormat:@"Warnings: %ld · Size failures: %@ · Limit: 19 MB", (long)warnings, values[@"size_failures"]],
        [NSString stringWithFormat:@"Continuations: %@ · Replays: %@ · Fallbacks: %@", values[@"continuation_requests"], values[@"full_replay_requests"], values[@"continuation_fallbacks"]],
        [NSString stringWithFormat:@"Tool replays: %@ · Saved: %.2f MB", values[@"tool_boundary_replays"], [values[@"saved_request_bytes"] doubleValue] / 1000000],
        [NSString stringWithFormat:@"Image resizes: %@ · Images: %@ · Image saved: %.2f MB", values[@"image_resize_requests"] ?: @0, values[@"resized_images"] ?: @0, [values[@"image_saved_request_bytes"] doubleValue] / 1000000],
    ]];
}

- (void)resizeChanged:(id)sender {
    [NSUserDefaults.standardUserDefaults setBool:self.resizeCheckbox.state == NSControlStateValueOn forKey:@"ResizeImagesOnLimit"];
}

- (void)pasteKeyClicked:(id)sender {
    NSString *value = [NSPasteboard.generalPasteboard stringForType:NSPasteboardTypeString];
    if (value.length > 0) {
        self.keyField.stringValue = [value stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
        [self.window makeFirstResponder:self.keyField];
        [self.keyField selectText:nil];
    } else {
        [self showAlert:@"The clipboard does not contain text."];
    }
}

- (NSArray<NSString *> *)serverArguments {
    NSMutableArray *arguments = [@[@"--port", [NSString stringWithFormat:@"%ld", (long)[self serverPort]], @"--log-level", @"info",
             @"--pid-file", self.pidURL.path, @"--continuation", @"auto"] mutableCopy];
    if (self.resizeCheckbox.state == NSControlStateValueOn) [arguments addObject:@"--resize-images-on-limit"];
    return arguments;
}

- (void)startClicked:(id)sender {
    if (self.serverTask != nil) return;
    NSString *apiKey = [self.keyField.stringValue stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    if (apiKey.length == 0) {
        apiKey = [self promptForAPIKey];
        if (apiKey.length == 0) return;
    }

    NSURL *executable = [NSBundle.mainBundle URLForResource:@"adapter-server" withExtension:nil];
    if (executable == nil) {
        [self showAlert:@"The bundled adapter server is missing. Reinstall the application."];
        return;
    }

    NSURL *appSupport = [NSFileManager.defaultManager URLsForDirectory:NSApplicationSupportDirectory inDomains:NSUserDomainMask].firstObject;
    NSURL *stateDirectory = [appSupport URLByAppendingPathComponent:[self stateName] isDirectory:YES];
    [NSFileManager.defaultManager createDirectoryAtURL:stateDirectory withIntermediateDirectories:YES attributes:nil error:nil];
    self.pidURL = [stateDirectory URLByAppendingPathComponent:@"server.pid"];
    [self stopRecordedServer];

    NSTask *task = [[NSTask alloc] init];
    task.executableURL = executable;
    task.arguments = [self serverArguments];
    NSMutableDictionary *environment = [NSProcessInfo.processInfo.environment mutableCopy];
    environment[@"PERPLEXITY_API_KEY"] = apiKey;
    task.environment = environment;

    NSURL *library = [NSFileManager.defaultManager URLsForDirectory:NSLibraryDirectory inDomains:NSUserDomainMask].firstObject;
    NSURL *logDirectory = [library URLByAppendingPathComponent:@"Logs" isDirectory:YES];
    [NSFileManager.defaultManager createDirectoryAtURL:logDirectory withIntermediateDirectories:YES attributes:nil error:nil];
    NSURL *logURL = [logDirectory URLByAppendingPathComponent:[[self stateName] stringByAppendingString:@".log"]];
    [NSFileManager.defaultManager createFileAtPath:logURL.path contents:[NSData data] attributes:nil];
    NSFileHandle *logHandle = [NSFileHandle fileHandleForWritingAtPath:logURL.path];
    task.standardOutput = logHandle;
    task.standardError = logHandle;

    __weak typeof(self) weakSelf = self;
    task.terminationHandler = ^(NSTask *finished) {
        dispatch_async(dispatch_get_main_queue(), ^{
            typeof(self) self = weakSelf;
            if (self == nil || self.serverTask != finished) return;
            self.serverTask = nil;
            [self setRunning:NO];
            if (finished.terminationStatus != 0) {
                [self setConnectionStatus:[NSString stringWithFormat:@"● Could not start — port %ld may be in use", (long)[self serverPort]] color:NSColor.systemRedColor];
            }
        });
    };

    NSError *error = nil;
    if (![task launchAndReturnError:&error]) {
        [self showAlert:[NSString stringWithFormat:@"The adapter could not start: %@", error.localizedDescription]];
        return;
    }
    self.serverTask = task;
    self.keyField.stringValue = @"";
    [self setRunning:YES];
    [self verifyHealth:0];
}

- (NSString *)promptForAPIKey {
    NSAlert *alert = [[NSAlert alloc] init];
    alert.messageText = @"Enter Perplexity API Key";
    alert.informativeText = @"The key is used only by the running adapter and is not saved.";
    [alert addButtonWithTitle:@"Start Adapter"];
    [alert addButtonWithTitle:@"Cancel"];

    NSSecureTextField *field = [[NSSecureTextField alloc] initWithFrame:NSMakeRect(0, 0, 340, 28)];
    field.placeholderString = @"pplx-…";
    field.editable = YES;
    field.selectable = YES;
    alert.accessoryView = field;
    alert.window.initialFirstResponder = field;

    if ([alert runModal] != NSAlertFirstButtonReturn) return @"";
    return [field.stringValue stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
}

- (void)stopClicked:(id)sender {
    [self stopServer];
    [self setRunning:NO];
}

- (void)stopServer {
    [self stopRecordedServer];
    if (self.serverTask.running) {
        [self.serverTask terminate];
        [self.serverTask waitUntilExit];
    }
    self.serverTask = nil;
}

- (void)stopRecordedServer {
    if (self.pidURL == nil) {
        NSURL *appSupport = [NSFileManager.defaultManager URLsForDirectory:NSApplicationSupportDirectory inDomains:NSUserDomainMask].firstObject;
        self.pidURL = [[appSupport URLByAppendingPathComponent:[self stateName] isDirectory:YES]
                       URLByAppendingPathComponent:@"server.pid"];
    }
    NSString *pidText = [NSString stringWithContentsOfURL:self.pidURL encoding:NSUTF8StringEncoding error:nil];
    pid_t pid = (pid_t)pidText.intValue;
    if (pid > 1 && kill(pid, 0) == 0) {
        kill(pid, SIGTERM);
        for (NSInteger attempt = 0; attempt < 20 && kill(pid, 0) == 0; attempt++) {
            usleep(50000);
        }
    }
    [NSFileManager.defaultManager removeItemAtURL:self.pidURL error:nil];
}

- (void)setRunning:(BOOL)running {
    [self.budgetTimer invalidate];
    self.budgetTimer = nil;
    if (running) {
        __weak typeof(self) weakSelf = self;
        self.budgetTimer = [NSTimer scheduledTimerWithTimeInterval:3 repeats:YES block:^(NSTimer *timer) {
            [weakSelf refreshBudget];
        }];
    }
    [self setBudgetRows:@[running ? @"Fetching adapter-wide statistics…" : @"Activity and request statistics appear while running."]];
    self.startButton.enabled = !running;
    self.stopButton.enabled = running;
    self.keyField.enabled = !running;
    self.resizeCheckbox.enabled = !running;
    [self setConnectionStatus:running ? @"● Starting…" : @"● Stopped" color:running ? NSColor.systemOrangeColor : NSColor.secondaryLabelColor];
}

- (void)refreshBudget {
    if (!self.serverTask.running) return;
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:[NSURL URLWithString:[NSString stringWithFormat:@"http://127.0.0.1:%ld/v1/adapter/status", (long)[self serverPort]]]];
    request.timeoutInterval = 2;
    request.cachePolicy = NSURLRequestReloadIgnoringLocalCacheData;
    NSString *token = NSProcessInfo.processInfo.environment[@"ADAPTER_LOCAL_TOKEN"] ?: @"local-adapter-token";
    [request setValue:[@"Bearer " stringByAppendingString:token] forHTTPHeaderField:@"Authorization"];
    __weak typeof(self) weakSelf = self;
    [[[NSURLSession sharedSession] dataTaskWithRequest:request completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        NSDictionary *values = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
        dispatch_async(dispatch_get_main_queue(), ^{
            typeof(self) self = weakSelf;
            if (!self || !self.serverTask.running) return;
            if ([(NSHTTPURLResponse *)response statusCode] != 200 || ![values isKindOfClass:NSDictionary.class]) {
                [self setBudgetRows:@[@"Adapter-wide statistics unavailable."]];
                return;
            }
            [self displayBudget:values];
        });
    }] resume];
}

- (void)verifyHealth:(NSInteger)attempt {
    if (!self.serverTask.running) return;
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:[NSURL URLWithString:[NSString stringWithFormat:@"http://127.0.0.1:%ld/health", (long)[self serverPort]]]];
    request.timeoutInterval = 1;
    __weak typeof(self) weakSelf = self;
    [[NSURLSession.sharedSession dataTaskWithRequest:request completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        dispatch_async(dispatch_get_main_queue(), ^{
            typeof(self) self = weakSelf;
            if (self == nil || !self.serverTask.running) return;
            NSInteger status = [(NSHTTPURLResponse *)response statusCode];
            if (status == 200 && data != nil) {
                [self setConnectionStatus:[NSString stringWithFormat:@"● Running at 127.0.0.1:%ld", (long)[self serverPort]] color:NSColor.systemGreenColor];
            } else if (attempt < 12) {
                dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.5 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
                    [self verifyHealth:attempt + 1];
                });
            } else {
                [self setConnectionStatus:@"● Server did not become ready" color:NSColor.systemRedColor];
            }
        });
    }] resume];
}

- (void)showAlert:(NSString *)message {
    NSAlert *alert = [[NSAlert alloc] init];
    alert.messageText = @"Codex–Perplexity Adapter";
    alert.informativeText = message;
    alert.alertStyle = NSAlertStyleWarning;
    [alert runModal];
}

@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSApplication *application = NSApplication.sharedApplication;
        AppDelegate *delegate = [[AppDelegate alloc] init];
        application.delegate = delegate;
        [application run];
    }
    return 0;
}
