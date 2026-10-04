import QtQuick
import QtQuick.Controls.Basic as Controls

Rectangle {
    id: root
    width: 900
    height: 46
    color: surface
    property var workspaces: []
    property int selectedPage: 2
    property color surface: "#171b24"
    property color foreground: "#eeeeee"
    property color muted: "#a8adb8"
    property color accent: "#4d8dff"
    property color selection: "#24334b"
    property color hoverSurface: "#2a303d"
    property color divider: "#363b46"
    property string uiFont: "sans-serif"
    readonly property bool compact: width < 720
    readonly property int activeIndex: {
        for (let i = 0; i < workspaces.length; ++i)
            if (workspaces[i].page === selectedPage) return i;
        return -1;
    }
    signal activated(int page)

    Rectangle {
        anchors.bottom: parent.bottom
        width: parent.width
        height: 1
        color: root.divider
    }
    ListView {
        id: destinations
        objectName: "workspaceList"
        anchors.fill: parent
        anchors.margins: 4
        visible: !root.compact
        orientation: ListView.Horizontal
        spacing: 2
        clip: true
        model: root.workspaces
        currentIndex: root.activeIndex
        onCurrentIndexChanged: positionViewAtIndex(currentIndex, ListView.Contain)
        onWidthChanged: positionViewAtIndex(currentIndex, ListView.Contain)
        delegate: Controls.Button {
            id: tab
            required property var modelData
            required property int index
            objectName: "workspace_" + modelData.page
            width: Math.max(80, implicitContentWidth + 28)
            height: 36
            text: modelData.label
            checkable: true
            checked: root.selectedPage === modelData.page
            Accessible.name: text + " workspace"
            Accessible.role: Accessible.PageTab
            Accessible.selected: checked
            onClicked: root.activated(modelData.page)
            Keys.onLeftPressed: {
                const next = Math.max(0, index - 1);
                root.activated(root.workspaces[next].page);
                destinations.itemAtIndex(next)?.forceActiveFocus();
            }
            Keys.onRightPressed: {
                const next = Math.min(root.workspaces.length - 1, index + 1);
                root.activated(root.workspaces[next].page);
                destinations.itemAtIndex(next)?.forceActiveFocus();
            }
            contentItem: Text {
                text: tab.text
                font.family: root.uiFont
                font.pixelSize: 13
                font.weight: tab.checked ? Font.DemiBold : Font.Normal
                color: tab.checked ? root.foreground : root.muted
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle {
                radius: 3
                color: tab.checked ? root.selection : tab.hovered ? root.hoverSurface : "transparent"
                border.width: tab.activeFocus ? 1 : 0
                border.color: root.accent
                Rectangle {
                    anchors.bottom: parent.bottom
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: parent.width - 18
                    height: 2
                    color: root.accent
                    visible: tab.checked
                }
            }
        }
        Controls.ScrollBar.horizontal: Controls.ScrollBar { height: 4 }
    }
    Text {
        id: workspaceLabel
        visible: root.compact
        anchors.left: parent.left
        anchors.leftMargin: 12
        anchors.verticalCenter: parent.verticalCenter
        text: "Workspace"
        font.family: root.uiFont
        font.pixelSize: 12
        color: root.muted
    }
    Controls.ComboBox {
        id: picker
        objectName: "workspacePicker"
        visible: root.compact
        anchors.left: workspaceLabel.right
        anchors.leftMargin: 12
        anchors.right: parent.right
        anchors.rightMargin: 8
        anchors.verticalCenter: parent.verticalCenter
        height: 34
        model: root.workspaces
        textRole: "label"
        currentIndex: root.activeIndex
        displayText: currentIndex < 0 ? "Tools workspace" : currentText
        font.family: root.uiFont
        font.pixelSize: 13
        Accessible.name: "Select workspace"
        palette.button: root.surface
        palette.buttonText: root.foreground
        palette.base: root.surface
        palette.text: root.foreground
        palette.highlight: root.selection
        palette.highlightedText: root.foreground
        onActivated: root.activated(root.workspaces[index].page)
    }
}
