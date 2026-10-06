import QtQuick
import QtQuick.Shapes

ShapePath {
    property string geometry: ""
    property bool dashed: false
    strokeStyle: dashed ? ShapePath.DashLine : ShapePath.SolidLine
    // Dashes and gaps of three line widths: sparser than Qt's default, so a dashed box still reads as a box.
    dashPattern: [3, 3]
    capStyle: ShapePath.SquareCap
    joinStyle: ShapePath.BevelJoin
    fillRule: ShapePath.OddEvenFill
    PathSvg { path: geometry }
}
