import QtQuick
import Quickshell
import qs.Common
import qs.Services
import qs.DCommon.Widgets
import qs.Modules.Plugins

PluginComponent {
    id: root

    property var popoutService: null
    property var _pendingSystemUpdateAnchor: null

    visibilityCommand: "/usr/lib/arch-niri-dux/release-update-check"
    visibilityInterval: 21600

    // Keep Dux's updater polling in the background now that the built-in
    // systemUpdate widget is off the bar.
    Ref {
        service: SystemUpdateService
    }

    // Source-grounded lazy open: PopoutService.toggleSystemUpdate no-ops
    // while systemUpdatePopout is null (DMSShell assigns both the loader and
    // the popout in Component.onCompleted; the loader starts inactive).
    // Arm the loader like DBarBody.triggerSystemUpdate does, then finish the
    // open when the popout assignment lands. No polling timer.
    Connections {
        target: root.popoutService
        function onSystemUpdatePopoutChanged() {
            if (!root._pendingSystemUpdateAnchor) {
                return;
            }
            if (!root.popoutService || !root.popoutService.systemUpdatePopout) {
                return;
            }
            var pending = root._pendingSystemUpdateAnchor;
            root._pendingSystemUpdateAnchor = null;
            root.popoutService.openSystemUpdate(pending[0], pending[1], pending[2], pending[3], pending[4]);
        }
    }

    Component.onDestruction: {
        _pendingSystemUpdateAnchor = null;
    }

    pillClickAction: (x, y, width, section, screen) => {
        if (!root.popoutService) {
            return;
        }
        if (root.popoutService.systemUpdatePopout) {
            root._pendingSystemUpdateAnchor = null;
            root.popoutService.toggleSystemUpdate(x, y, width, section, screen);
            return;
        }
        var loader = root.popoutService.systemUpdateLoader;
        if (!loader) {
            return;
        }
        loader.active = true;
        if (root.popoutService.systemUpdatePopout) {
            root.popoutService.toggleSystemUpdate(x, y, width, section, screen);
            return;
        }
        root._pendingSystemUpdateAnchor = [x, y, width, section, screen];
    }

    horizontalBarPill: Component {
        DIcon {
            name: "system_update_alt"
            color: Theme.primary
            size: root.iconSize
        }
    }

    verticalBarPill: Component {
        DIcon {
            name: "system_update_alt"
            color: Theme.primary
            size: root.iconSize
        }
    }
}
