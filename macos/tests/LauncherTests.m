#define main AdapterLauncherMain
#import "../AdapterLauncher.m"
#undef main

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
        if (label.hidden) continue;
        Check(NSWidth(label.frame) > 0, @"Missing label width");
        Check(NSHeight(label.frame) + 1 >= label.intrinsicContentSize.height, @"Clipped label height");
        Check(label.maximumNumberOfLines == 0, @"Wrapping label has a line limit");
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
                @{@"ReuseConversationContext": @([preference isEqualToString:@"true"])};
            [NSUserDefaults.standardUserDefaults setVolatileDomain:oldPreference forName:NSArgumentDomain];
            AppDelegate *delegate = [[AppDelegate alloc] init];
            [delegate buildWindow];
            delegate.pidURL = [NSURL fileURLWithPath:@"/private/tmp/launcher-test-unused.pid"];
            NSArray *arguments = [delegate serverArguments];
            Check([arguments.lastObject isEqualToString:@"auto"], @"Saved preference disabled reuse");
            for (NSView *view in delegate.contentStack.arrangedSubviews) {
                Check(![view isKindOfClass:NSButton.class], @"Unexpected checkbox");
            }
            Check(delegate.startButton.keyEquivalent.length == 1 && [delegate.startButton.keyEquivalent characterAtIndex:0] == 13, @"Return shortcut missing");
            CGFloat top = NSMaxY(delegate.window.frame);
            NSMutableArray *states = [NSMutableArray array];
            [states addObject:Inspect(delegate, @"stopped", top)];
            [delegate setRunning:YES];
            [states addObject:Inspect(delegate, @"starting", top)];
            [delegate setConnectionStatus:@"● Running at 127.0.0.1:4000" color:NSColor.systemGreenColor];
            NSDictionary *values = @{@"active_requests": @0, @"latest_completed_request_bytes": @8000,
                @"largest_active_request_bytes": @0, @"warnings_10mb": @0, @"warnings_12mb": @0,
                @"warnings_15mb": @0, @"size_failures": @0, @"continuation_requests": @12,
                @"full_replay_requests": @4, @"continuation_fallbacks": @0, @"tool_boundary_replays": @2,
                @"saved_request_bytes": @12345678};
            [delegate displayBudget:values];
            [states addObject:Inspect(delegate, @"running", top)];
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
            [delegate.budgetTimer invalidate];
            [delegate.window close];
        }
        NSData *data = [NSJSONSerialization dataWithJSONObject:reports options:NSJSONWritingPrettyPrinted error:nil];
        fwrite(data.bytes, 1, data.length, stdout);
        puts("");
    }
    return 0;
}
