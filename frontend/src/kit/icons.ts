import bookmark from "../../../assets/icons/bookmark.svg";
import check from "../../../assets/icons/check.svg";
import chevronDown from "../../../assets/icons/chevron-down.svg";
import chevronRight from "../../../assets/icons/chevron-right.svg";
import folder from "../../../assets/icons/folder.svg";
import layoutList from "../../../assets/icons/layout-list.svg";
import listVideo from "../../../assets/icons/list-video.svg";
import minus from "../../../assets/icons/minus.svg";
import palette from "../../../assets/icons/palette.svg";
import plug from "../../../assets/icons/plug.svg";
import search from "../../../assets/icons/search.svg";
import settings from "../../../assets/icons/settings.svg";
import square from "../../../assets/icons/square.svg";
import squarePlay from "../../../assets/icons/square-play.svg";
import video from "../../../assets/icons/video.svg";
import x from "../../../assets/icons/x.svg";

/** The bundled Lucide icons the UI uses; add one from `assets/icons/` here when a component needs it. */
export const ICONS = {
  bookmark,
  check,
  "chevron-down": chevronDown,
  "chevron-right": chevronRight,
  folder,
  "layout-list": layoutList,
  "list-video": listVideo,
  minus,
  palette,
  plug,
  search,
  settings,
  square,
  "square-play": squarePlay,
  video,
  x,
} as const;

export type IconName = keyof typeof ICONS;
