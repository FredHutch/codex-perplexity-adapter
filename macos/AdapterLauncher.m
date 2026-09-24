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
@property(strong) NSTextField *statusLabel;
@property(strong) NSTask *serverTask;
@property(strong) NSURL *pidURL;
@end

@implementation AppDelegate

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
    [self buildApplicationMenu];
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
    self.window = [[NSWindow alloc]
        initWithContentRect:NSMakeRect(0, 0, 540, 310)
                  styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable
                    backing:NSBackingStoreBuffered
                      defer:NO];
    self.window.title = @"Codex–Perplexity Adapter";
    self.window.releasedWhenClosed = NO;

    NSView *content = self.window.contentView;
    NSTextField *title = [self label:@"Codex–Perplexity Adapter"];
    title.font = [NSFont systemFontOfSize:24 weight:NSFontWeightSemibold];

    NSTextField *description = [NSTextField wrappingLabelWithString:
        @"Runs a private local Responses API at 127.0.0.1:4000 for Codex. Your Perplexity key stays in this process and is never saved."];
    description.textColor = NSColor.secondaryLabelColor;

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

    self.statusLabel = [self label:@"● Stopped"];
    self.statusLabel.textColor = NSColor.secondaryLabelColor;

    NSStackView *buttonRow = [NSStackView stackViewWithViews:@[self.startButton, self.stopButton, self.statusLabel]];
    buttonRow.orientation = NSUserInterfaceLayoutOrientationHorizontal;
    buttonRow.spacing = 10;
    buttonRow.alignment = NSLayoutAttributeCenterY;

    NSStackView *stack = [NSStackView stackViewWithViews:@[title, description, keyLabel, keyRow, buttonRow]];
    stack.orientation = NSUserInterfaceLayoutOrientationVertical;
    stack.alignment = NSLayoutAttributeLeading;
    stack.spacing = 12;
    stack.translatesAutoresizingMaskIntoConstraints = NO;
    [content addSubview:stack];

    [NSLayoutConstraint activateConstraints:@[
        [stack.leadingAnchor constraintEqualToAnchor:content.leadingAnchor constant:28],
        [stack.trailingAnchor constraintEqualToAnchor:content.trailingAnchor constant:-28],
        [stack.topAnchor constraintEqualToAnchor:content.topAnchor constant:28],
        [keyRow.widthAnchor constraintEqualToAnchor:stack.widthAnchor],
        [self.keyField.heightAnchor constraintEqualToConstant:28],
        [description.widthAnchor constraintEqualToAnchor:stack.widthAnchor],
    ]];
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
    NSURL *stateDirectory = [appSupport URLByAppendingPathComponent:@"Codex Perplexity Adapter" isDirectory:YES];
    [NSFileManager.defaultManager createDirectoryAtURL:stateDirectory withIntermediateDirectories:YES attributes:nil error:nil];
    self.pidURL = [stateDirectory URLByAppendingPathComponent:@"server.pid"];
    [self stopRecordedServer];

    NSTask *task = [[NSTask alloc] init];
    task.executableURL = executable;
    task.arguments = @[@"--port", @"4000", @"--log-level", @"info",
                       @"--pid-file", self.pidURL.path];
    NSMutableDictionary *environment = [NSProcessInfo.processInfo.environment mutableCopy];
    environment[@"PERPLEXITY_API_KEY"] = apiKey;
    task.environment = environment;

    NSURL *library = [NSFileManager.defaultManager URLsForDirectory:NSLibraryDirectory inDomains:NSUserDomainMask].firstObject;
    NSURL *logDirectory = [library URLByAppendingPathComponent:@"Logs" isDirectory:YES];
    [NSFileManager.defaultManager createDirectoryAtURL:logDirectory withIntermediateDirectories:YES attributes:nil error:nil];
    NSURL *logURL = [logDirectory URLByAppendingPathComponent:@"Codex Perplexity Adapter.log"];
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
                self.statusLabel.stringValue = @"● Could not start — port 4000 may be in use";
                self.statusLabel.textColor = NSColor.systemRedColor;
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
        self.pidURL = [[appSupport URLByAppendingPathComponent:@"Codex Perplexity Adapter" isDirectory:YES]
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
    self.startButton.enabled = !running;
    self.stopButton.enabled = running;
    self.keyField.enabled = !running;
    self.statusLabel.stringValue = running ? @"● Starting…" : @"● Stopped";
    self.statusLabel.textColor = running ? NSColor.systemOrangeColor : NSColor.secondaryLabelColor;
}

- (void)verifyHealth:(NSInteger)attempt {
    if (!self.serverTask.running) return;
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:[NSURL URLWithString:@"http://127.0.0.1:4000/health"]];
    request.timeoutInterval = 1;
    __weak typeof(self) weakSelf = self;
    [[NSURLSession.sharedSession dataTaskWithRequest:request completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        dispatch_async(dispatch_get_main_queue(), ^{
            typeof(self) self = weakSelf;
            if (self == nil || !self.serverTask.running) return;
            NSInteger status = [(NSHTTPURLResponse *)response statusCode];
            if (status == 200 && data != nil) {
                self.statusLabel.stringValue = @"● Running at 127.0.0.1:4000";
                self.statusLabel.textColor = NSColor.systemGreenColor;
            } else if (attempt < 12) {
                dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.5 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
                    [self verifyHealth:attempt + 1];
                });
            } else {
                self.statusLabel.stringValue = @"● Server did not become ready";
                self.statusLabel.textColor = NSColor.systemRedColor;
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
