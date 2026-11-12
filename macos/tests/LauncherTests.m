#define main AdapterLauncherMain
#import "../AdapterLauncher.m"
#undef main

@interface TestDelegate : AppDelegate
@property NSInteger testPort;
@property BOOL promptedWhileVisible;
@end
@implementation TestDelegate
- (NSInteger)serverPort { return self.testPort ?: 4000; }
- (NSString *)promptForAPIKey {
    self.promptedWhileVisible = self.window.visible;
    return @"";
}
@end

static void Check(BOOL condition, NSString *message) {
    if (!condition) {
        fprintf(stderr, "%s", message.UTF8String);
        exit(1);
    }
}

static NSDictionary *Inspect(AppDelegate *delegate, NSString *state, CGFloat top) {
    [delegate.window.contentView layoutSubtreeIfNeeded];
    NSRect content = delegate.window.contentView.bounds;
    NSRect stack = [delegate.contentStack convertRect:delegate.contentStack.bounds toView:delegate.window.contentView];
    CGFloat bottom = NSMinY(stack);
    Check(fabs(NSWidth(content) - 580) < 1, @"Window width changed");
    Check(fabs(bottom - 20) < 1, [NSString stringWithFormat:@"%@ bottom margin: %.2f", state, bottom]);
    Check(fabs(NSMaxY(delegate.window.frame) - top) < 1, @"Window top moved");
    Check(!delegate.contentStack.hasAmbiguousLayout, @"Ambiguous content stack");
    for (NSTextField *label in delegate.wrappingLabels) {
        if (label.hiddenOrHasHiddenAncestor) continue;
        Check(NSWidth(label.frame) > 0, @"Missing label width");
        Check(NSHeight(label.frame) + 1 >= label.intrinsicContentSize.height, @"Clipped label height");
        Check(label.maximumNumberOfLines == 0, @"Wrapping label has a line limit");
    }
    NSString *snapshots = NSProcessInfo.processInfo.environment[@"ADAPTER_TEST_SNAPSHOTS"];
    if (snapshots.length > 0) {
        NSBitmapImageRep *bitmap = [delegate.window.contentView bitmapImageRepForCachingDisplayInRect:content];
        [delegate.window.contentView cacheDisplayInRect:content toBitmapImageRep:bitmap];
        [[bitmap representationUsingType:NSBitmapImageFileTypePNG properties:@{}]
            writeToFile:[snapshots stringByAppendingPathComponent:[state stringByAppendingString:@".png"]] atomically:YES];
    }
    return @{ @"state": state, @"content_height": @(NSHeight(content)), @"bottom_margin": @(bottom),
              @"top_preserved": @YES, @"status": delegate.statusLabel.stringValue };
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        NSMutableArray *reports = [NSMutableArray array];
        for (NSString *preference in @[@"absent", @"true", @"false"]) {
            NSDictionary *oldPreference = [preference isEqualToString:@"absent"] ? @{} :
                @{@"ReuseConversationContext": @([preference isEqualToString:@"true"]), @"ResizeImagesOnLimit": @([preference isEqualToString:@"true"])};
            [NSUserDefaults.standardUserDefaults setVolatileDomain:oldPreference forName:NSArgumentDomain];
            AppDelegate *delegate = [[AppDelegate alloc] init];
            [delegate buildWindow];
            delegate.pidURL = [NSURL fileURLWithPath:@"/private/tmp/launcher-test-unused.pid"];
            NSArray *arguments = [delegate serverArguments];
            Check([arguments[1] isEqualToString:@"4000"], @"Expected regular port 4000");
            Check([arguments containsObject:@"auto"], @"Saved preference disabled reuse");
            Check([arguments containsObject:@"--resize-images-on-limit"], @"Saved preference disabled automatic resizing");
            for (NSView *view in delegate.contentStack.arrangedSubviews) {
                if ([view isKindOfClass:NSButton.class]) Check(view == delegate.detailsButton, @"Unexpected checkbox");
            }
            Check(delegate.startButton.keyEquivalent.length == 1 && [delegate.startButton.keyEquivalent characterAtIndex:0] == 13, @"Return shortcut missing");
            Check(delegate.detailsStack.hidden, @"Details must start collapsed");
            Check([delegate.detailsButton.accessibilityRole isEqual:NSAccessibilityDisclosureTriangleRole], @"Disclosure accessibility role missing");
            Check(!delegate.detailsButton.accessibilityExpanded, @"Disclosure incorrectly expanded");
            CGFloat collapsedHeight = NSHeight(delegate.window.contentView.bounds);
            CGFloat top = NSMaxY(delegate.window.frame);
            NSMutableArray *states = [NSMutableArray array];
            [states addObject:Inspect(delegate, @"stopped", top)];
            [delegate setRunning:YES];
            Check(delegate.detailsButton.enabled, @"Details unavailable while starting");
            [states addObject:Inspect(delegate, @"starting", top)];
            [delegate setConnectionStatus:@"● Running at 127.0.0.1:4000" color:NSColor.systemGreenColor];
            NSDictionary *values = @{@"active_requests": @0, @"latest_completed_request_bytes": @8000,
                @"largest_active_request_bytes": @0, @"warnings_10mb": @0, @"warnings_12mb": @0,
                @"warnings_15mb": @0, @"size_failures": @0, @"continuation_requests": @12,
                @"full_replay_requests": @4, @"continuation_fallbacks": @0, @"tool_boundary_replays": @2,
                @"saved_request_bytes": @12345678, @"image_resize_requests": @3, @"resized_images": @9, @"image_saved_request_bytes": @5500000};
            [delegate displayBudget:values];
            Check([[[delegate.budgetLabels valueForKey:@"stringValue"] componentsJoinedByString:@" "] containsString:@"Limit: 19 MB"], @"19 MB limit missing");
            [states addObject:Inspect(delegate, @"running-collapsed", top)];
            Check(NSHeight(delegate.window.contentView.bounds) == collapsedHeight, @"Hidden statistics reserve space");
            [delegate.detailsButton accessibilityPerformPress];
            Check(!delegate.detailsStack.hidden && delegate.detailsButton.accessibilityExpanded, @"Disclosure failed to expand");
            [states addObject:Inspect(delegate, @"running-expanded", top)];
            Check(NSWidth(delegate.statusLabel.frame) > 300, @"Status did not fill the remaining row width");
            Check(NSHeight(delegate.statusLabel.frame) < 25, @"Ordinary running status wrapped unnecessarily");
            CGFloat normalHeight = delegate.window.contentView.bounds.size.height;
            NSMutableDictionary *large = [values mutableCopy];
            for (NSString *key in large.allKeys) large[key] = @(999999999999999999LL);
            [delegate displayBudget:large];
            [states addObject:Inspect(delegate, @"large-counters", top)];
            Check(delegate.window.contentView.bounds.size.height > normalHeight, @"Large counters did not wrap");
            [delegate setBudgetRows:@[@"Adapter-wide statistics unavailable."]];
            [states addObject:Inspect(delegate, @"statistics-unavailable", top)];
            [delegate setRunning:NO];
            [states addObject:Inspect(delegate, @"stopped-again", top)];
            [delegate setConnectionStatus:@"● Could not start — port 4000 may be in use" color:NSColor.systemRedColor];
            [states addObject:Inspect(delegate, @"startup-error", top)];
            [delegate setConnectionStatus:@"● Server did not become ready. This deliberately long diagnostic must wrap without clipping the Start and Stop controls or leaving extra space below the statistics." color:NSColor.systemRedColor];
            [states addObject:Inspect(delegate, @"wrapped-status", top)];
            [reports addObject:@{@"saved_preference": preference, @"arguments": arguments, @"states": states}];
            [delegate.detailsButton performClick:nil];
            [states addObject:Inspect(delegate, @"error-collapsed", top)];
            [delegate stopBudgetPolling];
            [delegate.window orderOut:nil];
        }
        // Exercise AppKit lifecycle with a child owned only by this test.
        TestDelegate *delegate = [[TestDelegate alloc] init];
        [delegate buildApplicationMenu];
        [delegate buildStatusMenu];
        [delegate buildWindow];
        NSApp.delegate = delegate;
        [delegate showAdapter:nil];
        Check(delegate.window.visible && NSApp.activationPolicy == NSApplicationActivationPolicyRegular, @"Normal launch invisible");
        Check(!delegate.menuStop.enabled && delegate.menuStart.enabled, @"Stopped menu state wrong");
        Check(![delegate applicationShouldTerminateAfterLastWindowClosed:NSApp], @"Closing last window quits");
        NSArray *menuTitles = [delegate.statusItem.menu.itemArray valueForKey:@"title"];
        for (NSString *title in @[@"Show Adapter", @"Start Adapter", @"Stop Adapter", @"About", @"Quit"]) {
            Check([menuTitles containsObject:title], @"Menu item missing");
        }
        Check([[NSApp.windowsMenu itemAtIndex:0].keyEquivalent isEqualToString:@"w"], @"Command W missing");
        NSTask *child = [[NSTask alloc] init];
        child.executableURL = [NSURL fileURLWithPath:@"/usr/bin/python3"];
        child.arguments = @[@"-u", @"-c", @"import http.server,time,json; H=type('H',(http.server.BaseHTTPRequestHandler,),dict(do_GET=lambda x:(time.sleep(.4),x.send_response(200),x.end_headers(),x.wfile.write(b'{}')),log_message=lambda *a:None)); s=http.server.ThreadingHTTPServer(('127.0.0.1',0),H); print(s.server_port,flush=True); s.serve_forever()"];
        NSPipe *output = [NSPipe pipe];
        child.standardOutput = output;
        child.standardError = [NSFileHandle fileHandleWithNullDevice];
        Check([child launchAndReturnError:nil], @"Could not start isolated child");
        delegate.testPort = [[[[NSString alloc] initWithData:output.fileHandleForReading.availableData encoding:NSUTF8StringEncoding] stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet] integerValue];
        Check(delegate.testPort > 0, @"Mock did not bind an isolated port");
        delegate.serverTask = child;
        delegate.pidURL = [NSURL fileURLWithPath:[NSTemporaryDirectory() stringByAppendingPathComponent:NSUUID.UUID.UUIDString]];
        [delegate setRunning:YES];
        Check(delegate.budgetTimer == nil, @"Collapsed polling active");
        Check(!delegate.menuStart.enabled && delegate.menuStop.enabled, @"Running menu state wrong");
        [delegate.detailsButton performClick:nil];
        Check(delegate.budgetTimer != nil, [NSString stringWithFormat:@"Expanding did not refresh immediately: visible=%d mini=%d hidden=%d state=%ld running=%d", delegate.window.visible, delegate.window.miniaturized, NSApp.hidden, (long)delegate.detailsButton.state, child.running]);
        NSUInteger generation = delegate.budgetGeneration;
        Check([delegate acceptBudgetGeneration:generation server:child], @"Live callback rejected");
        __block BOOL requestFinished = NO;
        __block BOOL requestSucceeded = NO;
        NSURL *slowURL = [NSURL URLWithString:[NSString stringWithFormat:@"http://127.0.0.1:%ld/slow", (long)delegate.testPort]];
        [[NSURLSession.sharedSession dataTaskWithURL:slowURL completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
            dispatch_async(dispatch_get_main_queue(), ^{ requestSucceeded = [(NSHTTPURLResponse *)response statusCode] == 200; requestFinished = YES; });
        }] resume];
        [delegate.window performClose:nil];
        Check(!delegate.window.visible && NSApp.activationPolicy == NSApplicationActivationPolicyAccessory, @"Close did not hide to menu bar");
        Check(child.running && delegate.budgetTimer == nil && delegate.budgetRequest == nil, @"Close stopped child or left polling active");
        Check(![delegate acceptBudgetGeneration:generation server:child], @"Stale callback accepted after hide");
        [delegate showAdapter:nil];
        Check(delegate.window.visible && NSApp.activationPolicy == NSApplicationActivationPolicyRegular, @"Show did not restore Dock/window");
        Check(!delegate.detailsStack.hidden && delegate.budgetTimer != nil, @"Show lost details state/polling");
        NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:5];
        while (!requestFinished && deadline.timeIntervalSinceNow > 0) {
            [NSRunLoop.currentRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.01]];
        }
        Check(requestFinished && requestSucceeded && child.running, @"Hide/show interrupted an active request");
        generation = delegate.budgetGeneration;
        [delegate.detailsButton performClick:nil];
        Check(delegate.budgetTimer == nil && ![delegate acceptBudgetGeneration:generation server:child], @"Collapse accepted stale callback");
        [delegate hideToMenuBar:nil];
        [delegate showAdapter:nil];
        Check(delegate.detailsStack.hidden && child.running, @"Hide/show changed collapsed state or child");
        [delegate applicationWillTerminate:[NSNotification notificationWithName:NSApplicationWillTerminateNotification object:NSApp]];
        Check(!child.running && delegate.serverTask == nil, @"Quit did not stop child");
        [delegate hideToMenuBar:nil];
        [delegate startFromMenu:nil];
        Check(delegate.promptedWhileVisible, @"Menu Start did not reveal key entry");
        [reports addObject:@{@"lifecycle": @"close/show/Dock/menu/quit", @"passed": @YES}];
        [delegate.window orderOut:nil];
        NSData *data = [NSJSONSerialization dataWithJSONObject:reports options:NSJSONWritingPrettyPrinted error:nil];
        fwrite(data.bytes, 1, data.length, stdout);
        puts("");
    }
    return 0;
}
