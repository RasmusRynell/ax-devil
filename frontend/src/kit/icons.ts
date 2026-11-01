import bookmark from "../../../assets/icons/bookmark.svg";
import check from "../../../assets/icons/check.svg";
import chevronDown from "../../../assets/icons/chevron-down.svg";
import chevronLeft from "../../../assets/icons/chevron-left.svg";
import chevronRight from "../../../assets/icons/chevron-right.svg";
import eye from "../../../assets/icons/eye.svg";
import eyeOff from "../../../assets/icons/eye-off.svg";
import folder from "../../../assets/icons/folder.svg";
import folderOpen from "../../../assets/icons/folder-open.svg";
import layers from "../../../assets/icons/layers.svg";
import layoutList from "../../../assets/icons/layout-list.svg";
import listVideo from "../../../assets/icons/list-video.svg";
import minus from "../../../assets/icons/minus.svg";
import palette from "../../../assets/icons/palette.svg";
import pause from "../../../assets/icons/pause.svg";
import play from "../../../assets/icons/play.svg";
import plug from "../../../assets/icons/plug.svg";
import rotateCcw from "../../../assets/icons/rotate-ccw.svg";
import rotateCw from "../../../assets/icons/rotate-cw.svg";
import search from "../../../assets/icons/search.svg";
import settings from "../../../assets/icons/settings.svg";
import slidersHorizontal from "../../../assets/icons/sliders-horizontal.svg";
import square from "../../../assets/icons/square.svg";
import squarePlay from "../../../assets/icons/square-play.svg";
import stepBack from "../../../assets/icons/step-back.svg";
import stepForward from "../../../assets/icons/step-forward.svg";
import triangleAlert from "../../../assets/icons/triangle-alert.svg";
import video from "../../../assets/icons/video.svg";
import x from "../../../assets/icons/x.svg";

/** The bundled Lucide icons the UI uses; add one from `assets/icons/` here when a component needs it. */
export const ICONS = {
  bookmark,
  check,
  "chevron-down": chevronDown,
  "chevron-left": chevronLeft,
  "chevron-right": chevronRight,
  eye,
  "eye-off": eyeOff,
  folder,
  "folder-open": folderOpen,
  layers,
  "layout-list": layoutList,
  "list-video": listVideo,
  minus,
  palette,
  pause,
  play,
  plug,
  "rotate-ccw": rotateCcw,
  "rotate-cw": rotateCw,
  search,
  settings,
  "sliders-horizontal": slidersHorizontal,
  square,
  "square-play": squarePlay,
  "step-back": stepBack,
  "step-forward": stepForward,
  "triangle-alert": triangleAlert,
  video,
  x,
} as const;

export type IconName = keyof typeof ICONS;
