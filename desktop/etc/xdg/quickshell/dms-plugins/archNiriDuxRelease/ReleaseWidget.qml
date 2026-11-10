import QtQuick
import qs.Common
import qs.Services
import qs.DCommon.Widgets
import qs.Modules.Plugins

PluginComponent {
    id: root

    property var popoutService: null
    property var _pendingSystemUpdateAnchor: null

    // The single source of truth is Dux's own updater state: the button shows
    // only while its scheduled checks report a newer arch-niri-dux release.
    // PluginComponent re-runs visibilityCommand whenever it changes
    // (PluginComponent.qml onVisibilityCommandChanged) and maps exit 0 to
    // visible, so binding the command to this property flips visibility with
    // no polling interval, no checker script and no extra repository download.
    readonly property bool hasRelease: SystemUpdateService.availableUpdates.some(update => update.name === "arch-niri-dux")
    visibilityCommand: hasRelease ? "true" : "false"

    // Keep Dux's updater polling in the background now that the built-in
    // systemUpdate widget is off the bar. Without a Ref holder, QML never
    // fetches state and `dms ipc call systemupdater updatestatus` reports
    // "no package manager available".
    Ref {
        service: SystemUpdateService
    }

    // Source-grounded lazy open: PopoutService.toggleSystemUpdate no-ops
    // while systemUpdatePopout is null (DMSShell assigns both the loader and
    // the popout on completion; the loader starts inactive). Arm the loader
    // like DBarBody.triggerSystemUpdate does, then finish the open when the
    // popout assignment lands. No polling timer.
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
        // If the loader finished synchronously the popout is already set;
        // otherwise the change handler above finishes the open when it lands.
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
