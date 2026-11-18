import type { WarningInfo } from "../generated/api";

/** One thing to tell the user: a problem, what follows from it and the reasons the backend gives. */
export type Notice = {
  problem: string;
  consequence?: string;
  reasons?: readonly string[];
  /** Whether more reasons exist than `reasons` lists. */
  more?: boolean;
};

/** The notice for a warning the backend sent. */
export function noticeOf(warning: WarningInfo): Notice {
  return {
    problem: warning.problem,
    consequence: warning.consequence,
    reasons: warning.reasons,
    more: warning.more_reasons,
  };
}

/** Notices as a list: each problem with what follows from it and the reasons the backend gives. */
export function NoticeList({ notices }: { notices: readonly Notice[] }) {
  return (
    <ul className="m-0 flex list-none flex-col gap-m p-0">
      {notices.map((notice, index) => (
        <li key={index} className="flex flex-col gap-xs">
          <span className="font-semibold break-words">{notice.problem}</span>
          {notice.consequence && <span className="break-words">{notice.consequence}</span>}
          {notice.reasons?.map((reason, at) => (
            <span key={at} className="text-small break-all text-muted">
              {reason}
            </span>
          ))}
          {notice.more && <span className="text-small text-muted">More are not listed</span>}
        </li>
      ))}
    </ul>
  );
}
