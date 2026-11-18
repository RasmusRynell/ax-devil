import { MenuButton } from "../kit/Button";
import { SearchField } from "../kit/Fields";
import { IconToggle } from "../kit/IconToggle";
import type { MenuCommand, MenuEntry } from "../kit/Menu";
import { ORDERS, type Order } from "./network";
import { facetKey, type Facet } from "./rows";

const ORDER_IDS: ReadonlySet<string> = new Set(ORDERS.map(([id]) => id));

interface Props {
  query: string;
  onQuery: (query: string) => void;
  order: Order;
  onOrder: (order: Order) => void;
  /** The facets the Filter offers, grouped, and the keys of those the user hid. */
  facets: readonly Facet[];
  hidden: ReadonlySet<string>;
  onToggle: (facet: Facet) => void;
  /** Whether the hidden items show, and a change of it. */
  showHidden: boolean;
  onShowHidden: (shown: boolean) => void;
}

/** Search, Sort, Filter and Show hidden items over the Live view's rows. The Filter appears once there is a choice to make. */
export function LiveToolbar({
  query,
  onQuery,
  order,
  onOrder,
  facets,
  hidden,
  onToggle,
  showHidden,
  onShowHidden,
}: Props) {
  const orders: MenuEntry[] = ORDERS.map(([id, label]) => ({ kind: "check", id, label }));
  const filters: MenuEntry[] = facets.flatMap((facet, at) => [
    ...(facet.group === facets[at - 1]?.group
      ? []
      : [{ kind: "heading" as const, label: facet.group }]),
    { kind: "check" as const, id: facetKey(facet), label: facet.value },
  ]);
  const shown = new Set(facets.map(facetKey).filter((key) => !hidden.has(key)));
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-s px-m pt-s pb-xs">
      <SearchField
        placeholder="Search"
        query={{ value: query, onChange: onQuery }}
        className="min-w-search-least flex-1"
      />
      <MenuButton
        label="Sort"
        alignEnd
        title="The order of the rows"
        entries={orders}
        checked={new Set([order])}
        onSelect={({ id }: MenuCommand) => ORDER_IDS.has(id) && onOrder(id as Order)}
      />
      {facets.length > 0 && (
        <MenuButton
          label="Filter"
          alignEnd
          title="Show or hide rows by what they are"
          entries={filters}
          checked={shown}
          onSelect={({ id }: MenuCommand) => {
            const facet = facets.find((candidate) => facetKey(candidate) === id);
            if (facet) onToggle(facet);
          }}
        />
      )}
      <IconToggle
        icon="eye"
        label="Show hidden items"
        pressed={showHidden}
        onChange={onShowHidden}
      />
    </div>
  );
}
