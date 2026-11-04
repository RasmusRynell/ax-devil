import { Dialog } from "../kit/Dialog";
import type { Information } from "../live/information";

/** One titled list of values; the lists under it are indented. */
function Block({ info, level }: { info: Information; level: number }) {
  return (
    <>
      <tr>
        <th
          colSpan={2}
          scope="colgroup"
          style={{ paddingLeft: `calc(var(--spacing-m) * ${level * 2})` }}
          className="px-m py-xs text-left font-semibold break-words"
        >
          {info.title}
        </th>
      </tr>
      {info.fields.map(([label, value]) => (
        <tr key={label}>
          <th
            scope="row"
            style={{ paddingLeft: `calc(var(--spacing-m) * ${level * 2 + 2})` }}
            className="px-m py-xs text-left font-normal text-muted whitespace-nowrap"
          >
            {label}
          </th>
          <td className="px-m py-xs break-words select-text">{value}</td>
        </tr>
      ))}
      {info.children.map((child) => (
        <Block key={child.title} info={child} level={level + 1} />
      ))}
    </>
  );
}

/** The information of a workspace item: its settings as a list of labelled values, with the lists of its lanes under it. */
export function InformationDialog({ info, onClose }: { info: Information; onClose: () => void }) {
  return (
    <Dialog title={info.title} onOk={onClose} okLabel="Close" focusOk>
      <div className="max-h-[60vh] min-w-[40ch] overflow-auto">
        <table className="w-full border-collapse text-body">
          <thead>
            <tr className="border-b border-line text-left text-muted">
              <th scope="col" className="px-m py-xs font-normal">
                Item
              </th>
              <th scope="col" className="px-m py-xs font-normal">
                Value
              </th>
            </tr>
          </thead>
          <tbody>
            <Block info={info} level={0} />
          </tbody>
        </table>
      </div>
    </Dialog>
  );
}
