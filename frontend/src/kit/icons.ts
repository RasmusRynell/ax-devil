import bookmark from "../../../assets/icons/bookmark.svg";
import check from "../../../assets/icons/check.svg";
import chevronDown from "../../../assets/icons/chevron-down.svg";
import chevronLeft from "../../../assets/icons/chevron-left.svg";
import chevronRight from "../../../assets/icons/chevron-right.svg";
import chevronUp from "../../../assets/icons/chevron-up.svg";
import circleHelp from "../../../assets/icons/circle-help.svg";
import circlePlay from "../../../assets/icons/circle-play.svg";
import clock from "../../../assets/icons/clock.svg";
import copy from "../../../assets/icons/copy.svg";
import crown from "../../../assets/icons/crown.svg";
import download from "../../../assets/icons/download.svg";
import ellipsis from "../../../assets/icons/ellipsis.svg";
import eye from "../../../assets/icons/eye.svg";
import eyeOff from "../../../assets/icons/eye-off.svg";
import fileText from "../../../assets/icons/file-text.svg";
import folder from "../../../assets/icons/folder.svg";
import folderOpen from "../../../assets/icons/folder-open.svg";
import funnel from "../../../assets/icons/funnel.svg";
import gripVertical from "../../../assets/icons/grip-vertical.svg";
import info from "../../../assets/icons/info.svg";
import layers from "../../../assets/icons/layers.svg";
import layoutList from "../../../assets/icons/layout-list.svg";
import listVideo from "../../../assets/icons/list-video.svg";
import minus from "../../../assets/icons/minus.svg";
import mousePointer2 from "../../../assets/icons/mouse-pointer-2.svg";
import palette from "../../../assets/icons/palette.svg";
import pause from "../../../assets/icons/pause.svg";
import pin from "../../../assets/icons/pin.svg";
import pinFilled from "../../../assets/icons/pin-filled.svg";
import play from "../../../assets/icons/play.svg";
import plug from "../../../assets/icons/plug.svg";
import plus from "../../../assets/icons/plus.svg";
import radio from "../../../assets/icons/radio.svg";
import refreshCw from "../../../assets/icons/refresh-cw.svg";
import rotateCcw from "../../../assets/icons/rotate-ccw.svg";
import rotateCw from "../../../assets/icons/rotate-cw.svg";
import search from "../../../assets/icons/search.svg";
import settings from "../../../assets/icons/settings.svg";
import slidersHorizontal from "../../../assets/icons/sliders-horizontal.svg";
import square from "../../../assets/icons/square.svg";
import squarePlay from "../../../assets/icons/square-play.svg";
import stepBack from "../../../assets/icons/step-back.svg";
import stepForward from "../../../assets/icons/step-forward.svg";
import trash2 from "../../../assets/icons/trash-2.svg";
import triangleAlert from "../../../assets/icons/triangle-alert.svg";
import users from "../../../assets/icons/users.svg";
import video from "../../../assets/icons/video.svg";
import videoDashed from "../../../assets/icons/video-dashed.svg";
import wifi from "../../../assets/icons/wifi.svg";
import x from "../../../assets/icons/x.svg";

/** The bundled Lucide icons the UI uses (`pin-filled` is `pin` with a solid shape); add one from `assets/icons/` here when a component needs it. */
export const ICONS = {
  bookmark,
  check,
  "chevron-down": chevronDown,
  "chevron-left": chevronLeft,
  "chevron-right": chevronRight,
  "chevron-up": chevronUp,
  "circle-help": circleHelp,
  "circle-play": circlePlay,
  clock,
  copy,
  crown,
  download,
  ellipsis,
  eye,
  "eye-off": eyeOff,
  "file-text": fileText,
  folder,
  "folder-open": folderOpen,
  funnel,
  "grip-vertical": gripVertical,
  info,
  layers,
  "layout-list": layoutList,
  "list-video": listVideo,
  minus,
  "mouse-pointer-2": mousePointer2,
  palette,
  pause,
  pin,
  "pin-filled": pinFilled,
  play,
  plug,
  plus,
  radio,
  "refresh-cw": refreshCw,
  "rotate-ccw": rotateCcw,
  "rotate-cw": rotateCw,
  search,
  settings,
  "sliders-horizontal": slidersHorizontal,
  square,
  "square-play": squarePlay,
  "step-back": stepBack,
  "step-forward": stepForward,
  "trash-2": trash2,
  "triangle-alert": triangleAlert,
  users,
  video,
  "video-dashed": videoDashed,
  wifi,
  x,
} as const;

export type IconName = keyof typeof ICONS;
