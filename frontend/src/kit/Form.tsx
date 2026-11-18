import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { BrowseButton } from "./Button";
import { Checkbox, useSelectOnOpen } from "./Fields";
import { Icon } from "./Icon";
import { FIELD, FIELD_IN } from "./look";
import { ownKeys } from "./keys";
import { Refusal } from "./Refusal";
import { SectionCaption } from "./SectionCaption";

/** Rows of a label and its control: one label column as wide as the longest label, up to `spacing-label`, the controls filling the rest. */
const GRID =
  "grid min-w-0 grid-cols-[fit-content(var(--spacing-label))_minmax(0,1fr)] gap-x-l gap-y-s @max-sm:grid-cols-[minmax(0,1fr)]";

/**
 * A form of labelled rows. The labels sit in one column left of the controls; in a container too narrow for both,
 * each label sits over its control. Rows are `FormInput`, `FormPath`, `FormSelect`, `FormCombo`, `FormRow` around any
 * other control, `FormCheck`, with `FormSection` captions between them. Text boxes, drop-downs and paths fill the
 * control column; a segmented control keeps its own width at its left.
 */
export function Form({ children }: { children: ReactNode }) {
  return (
    <div className="@container min-w-0">
      <div className={GRID}>{children}</div>
    </div>
  );
}

/**
 * A caption across the whole form, flush with the labels, that names the group of rows after it. With `children`
 * the rows are inside it, a group that shares the form's columns and is named by the caption; `action` sits at the
 * caption's right edge.
 */
export function FormSection({
  heading,
  action,
  children,
}: {
  heading: string;
  action?: ReactNode;
  children?: ReactNode;
}) {
  const grouped = children !== undefined;
  const caption = (
    <div className={`col-span-full -ml-m ${grouped ? "" : "mt-m first:mt-0"}`}>
      <SectionCaption heading={heading} action={action} plain />
    </div>
  );
  return grouped ? (
    <div
      role="group"
      aria-label={heading}
      className="col-span-full mt-m grid grid-cols-subgrid gap-y-s first:mt-0"
    >
      {caption}
      {children}
    </div>
  ) : (
    caption
  );
}

/**
 * Pages of rows that share the form's columns, so their labels line up with the rows around them. Only the `current`
 * page shows, and the stack is as tall as its tallest page, so the form keeps its size from page to page. Put it in
 * a box that scrolls when the window cannot hold that height: the scrollbar then belongs to the box, not to a page.
 * With a `panelId` the pages are the panels of tabs: the current
 * one is the tab panel with that id.
 */
export function FormStack({
  current,
  pages,
  panelId,
}: {
  current: string;
  pages: Record<string, ReactNode>;
  panelId?: string;
}) {
  return (
    <div className="col-span-full grid grid-cols-subgrid">
      {Object.entries(pages).map(([name, rows]) => {
        const shown = name === current;
        return (
          <div
            key={name}
            id={panelId && shown ? panelId : undefined}
            role={panelId && shown ? "tabpanel" : undefined}
            aria-label={panelId && shown ? name : undefined}
            inert={!shown}
            aria-hidden={shown ? undefined : true}
            // Positioned, so the hidden radios of a toggle further down stay in their page.
            className={`relative col-span-full row-start-1 grid grid-cols-subgrid content-start gap-y-s ${shown ? "" : "invisible"}`}
          >
            {rows}
          </div>
        );
      })}
    </div>
  );
}

interface RowProps {
  label: string;
  /** What sits after the label, such as a mark. */
  marker?: ReactNode;
  /** The id of the control, which the label names. */
  id?: string;
  /** The control, then anything that sits after it in the row. */
  children: ReactNode;
  /** A line under the control, such as a question about it. */
  note?: ReactNode;
  /** Why the value is refused, under the note; the control with this `id` is then invalid and described by it. */
  error?: string;
}

/** The id of the refusal in the row whose control has `id`. */
const errorId = (id: string) => `${id}-error`;

/** What a control says about itself: whether its row needs a value, and the refusal that describes it. */
function aria(
  id: string,
  { required, error, describedBy }: { required?: boolean; error?: string; describedBy?: string },
) {
  return {
    "aria-required": required || undefined,
    "aria-invalid": error ? true : undefined,
    "aria-describedby":
      [describedBy, error ? errorId(id) : undefined].filter(Boolean).join(" ") || undefined,
  };
}

/** A label and any control: the row the other `Form` rows are made of. `id` is the control's, when it can be named. */
export function FormRow({ label, marker, id, children, note, error }: RowProps) {
  return (
    <>
      <label
        htmlFor={id}
        className="flex min-h-row min-w-0 items-center gap-s self-start [overflow-wrap:anywhere]"
      >
        {label}
        {marker}
      </label>
      <div className="flex min-w-0 flex-col gap-xs">
        <div className="flex min-w-0 items-center gap-s">{children}</div>
        {note}
        {error && <Refusal id={id && errorId(id)} reason={error} />}
      </div>
    </>
  );
}

/** A box to tick across the whole row, its label after it, so a long label never widens the label column. */
export function FormCheck({
  label,
  marker,
  checked,
  onChange,
  disabled,
  title,
  note,
}: {
  label: string;
  marker?: ReactNode;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  title?: string;
  note?: ReactNode;
}) {
  return (
    <div className="col-span-full flex min-w-0 flex-col gap-xs">
      <Checkbox
        leading
        label={
          <span className="flex items-center gap-s [overflow-wrap:anywhere]">
            {label}
            {marker}
          </span>
        }
        checked={checked}
        onChange={onChange}
        disabled={disabled}
        title={title}
        className="min-h-row"
      />
      {note}
    </div>
  );
}

interface Common {
  label: string;
  marker?: ReactNode;
  /** Sits after the control. */
  extra?: ReactNode;
  note?: ReactNode;
  /** The value is needed: the control says so to assistive technology. */
  required?: boolean;
  /** Why the value is refused; shown under the control, which is then invalid. */
  error?: string;
  disabled?: boolean;
  /** Shown as a tooltip on the control. */
  title?: string;
}

/**
 * A labelled text box that fills its row; `masked` hides what is typed, `suggestions` are offered to pick from, each
 * as `[value, name]`, while any text stays allowed, and `describedBy` names a note about it.
 */
export function FormInput({
  label,
  marker,
  value,
  onChange,
  placeholder,
  masked = false,
  selectOnOpen = false,
  suggestions,
  describedBy,
  extra,
  note,
  required,
  error,
  disabled,
  title,
}: Common & {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  masked?: boolean;
  /** Takes the focus with its text selected once it is on screen, as an edit of this value starts. */
  selectOnOpen?: boolean;
  suggestions?: readonly (readonly [string, string])[];
  describedBy?: string;
}) {
  const id = useId();
  const list = `${id}-suggestions`;
  const input = useSelectOnOpen(selectOnOpen);
  return (
    <FormRow label={label} marker={marker} id={id} note={note} error={error}>
      <input
        ref={input}
        id={id}
        type={masked ? "password" : "text"}
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        title={title}
        autoComplete="off"
        spellCheck={false}
        list={suggestions?.length ? list : undefined}
        {...aria(id, { required, error, describedBy })}
        onChange={(event) => onChange(event.target.value)}
        className={`${FIELD} min-w-0 flex-1`}
      />
      {suggestions?.length ? (
        <datalist id={list}>
          {suggestions.map(([suggestion, name]) => (
            <option key={suggestion} value={suggestion} label={name} />
          ))}
        </datalist>
      ) : null}
      {extra}
    </FormRow>
  );
}

/** A `FormInput` for a path, with the folder button after it where `browse` says how to choose one. */
export function FormPath({
  what,
  browse,
  ...input
}: Parameters<typeof FormInput>[0] & {
  /** What the button chooses, as in "Choose folder". */
  what: string;
  /** Runs the choice; without it there is no button. */
  browse?: () => void;
}) {
  return <FormInput {...input} extra={browse && <BrowseButton what={what} onBrowse={browse} />} />;
}

/** A labelled drop-down over a closed set; `failed` marks the chosen status line as an error. */
export function FormSelect<T extends string>({
  label,
  marker,
  value,
  options,
  onChange,
  failed = false,
  extra,
  note,
  required,
  error,
  disabled,
  title,
}: Common & {
  value: T;
  /** Each choice as `[value, label]`. */
  options: readonly (readonly [T, string])[];
  onChange: (value: T) => void;
  failed?: boolean;
}) {
  const id = useId();
  return (
    <FormRow label={label} marker={marker} id={id} note={note} error={error}>
      <span className="relative flex min-w-0 flex-1 items-center">
        {failed && (
          <Icon name="triangle-alert" className="pointer-events-none absolute left-s text-danger" />
        )}
        <select
          id={id}
          value={value}
          disabled={disabled}
          title={title}
          {...aria(id, { required, error })}
          onChange={(event) => onChange(event.target.value as T)}
          className={`${FIELD} min-w-0 flex-1 ${failed ? "pl-row text-danger" : ""}`}
        >
          {options.map(([option, name]) => (
            <option key={option} value={option}>
              {name}
            </option>
          ))}
        </select>
      </span>
      {extra}
    </FormRow>
  );
}

/** One line of a `FormCombo` list; a disabled one is a status, not a choice. */
export interface ComboOption {
  value: string;
  label: string;
  disabled?: boolean;
}

/** The most of the window height a list of choices takes. */
const LIST_SHARE = 0.4;

const KEYS = new Set(["ArrowDown", "ArrowUp", "Enter", "Escape"]);

/**
 * A labelled text box that also lists choices: any text stays allowed, and picking a line puts its value in the box.
 * `onOpen` runs as the list opens, so the caller can look again for what it lists.
 */
export function FormCombo({
  label,
  marker,
  value,
  onChange,
  placeholder,
  options,
  onOpen,
  note,
  required,
  error,
  disabled,
}: Common & {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  options: readonly ComboOption[];
  onOpen?: () => void;
}) {
  const id = useId();
  const listId = `${id}-list`;
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const box = useRef<HTMLSpanElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const field = useRef<HTMLInputElement>(null);

  const reveal = () => {
    if (open || disabled) return;
    onOpen?.();
    setActive(
      Math.max(
        0,
        options.findIndex((option) => !option.disabled),
      ),
    );
    setOpen(true);
  };
  const choose = (option: ComboOption | undefined) => {
    if (!option || option.disabled) return;
    onChange(option.value);
    setOpen(false);
  };
  const move = (step: number) => {
    const free = options.flatMap((option, index) => (option.disabled ? [] : [index]));
    if (!free.length) return;
    const at = free.indexOf(active);
    setActive(free[(at + step + free.length) % free.length]);
  };

  // The list floats at the box, so a form that scrolls does not clip it. It opens below, or above when the room
  // below is smaller than the list needs and the room above is larger, and is never taller than its side of the window.
  useLayoutEffect(() => {
    const anchor = box.current?.getBoundingClientRect();
    const popup = list.current;
    if (!open || !anchor || !popup) return;
    const gap = parseFloat(getComputedStyle(popup).marginTop) || 0;
    const below = window.innerHeight - anchor.bottom - 2 * gap;
    const above = anchor.top - 2 * gap;
    const wanted = Math.min(popup.scrollHeight + 2, window.innerHeight * LIST_SHARE);
    const up = below < wanted && above > below;
    popup.style.left = `${anchor.left}px`;
    popup.style.width = `${anchor.width}px`;
    popup.style.top = up ? "auto" : `${anchor.bottom}px`;
    popup.style.bottom = up ? `${window.innerHeight - anchor.top}px` : "auto";
    popup.style.maxHeight = `${Math.max(0, Math.min(up ? above : below, window.innerHeight * LIST_SHARE))}px`;
  }, [open, options]);
  // The active option stays in view as the arrows move through a list that scrolls.
  useLayoutEffect(() => {
    const popup = list.current;
    const row = popup?.children[active] as HTMLElement | undefined;
    if (!open || !popup || !row) return;
    if (row.offsetTop < popup.scrollTop) popup.scrollTop = row.offsetTop;
    else if (row.offsetTop + row.offsetHeight > popup.scrollTop + popup.clientHeight)
      popup.scrollTop = row.offsetTop + row.offsetHeight - popup.clientHeight;
  }, [open, active]);
  useEffect(() => {
    if (!open) return;
    const away = (event: Event) => {
      if (
        !box.current?.contains(event.target as Node) &&
        !list.current?.contains(event.target as Node)
      )
        setOpen(false);
    };
    document.addEventListener("pointerdown", away);
    document.addEventListener("scroll", away, true);
    window.addEventListener("resize", away);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("scroll", away, true);
      window.removeEventListener("resize", away);
    };
  }, [open]);

  const onKeyDown = (event: KeyboardEvent) => {
    ownKeys(KEYS)(event);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (open) move(event.key === "ArrowDown" ? 1 : -1);
      else reveal();
    } else if (open && event.key === "Enter") {
      event.preventDefault();
      choose(options[active]);
    } else if (open && event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
    }
  };

  return (
    <FormRow label={label} marker={marker} id={id} note={note} error={error}>
      <span
        ref={box}
        onBlur={(event) => {
          const next = event.relatedTarget as Node | null;
          if (!next || !box.current?.contains(next)) setOpen(false);
        }}
        className={`flex min-w-0 flex-1 items-center gap-s ${FIELD_IN}`}
      >
        <input
          ref={field}
          id={id}
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="none"
          aria-activedescendant={open ? `${id}-${active}` : undefined}
          {...aria(id, { required, error })}
          value={value}
          placeholder={placeholder}
          disabled={disabled}
          autoComplete="off"
          spellCheck={false}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={onKeyDown}
          className="min-w-0 flex-1 bg-transparent text-fg outline-none placeholder:text-muted disabled:text-disabled"
        />
        <button
          type="button"
          tabIndex={-1}
          aria-label={`Show ${label.toLowerCase()} choices`}
          disabled={disabled}
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => {
            field.current?.focus();
            if (open) setOpen(false);
            else reveal();
          }}
          className="flex text-muted enabled:hover:text-fg disabled:text-disabled"
        >
          <Icon name="chevron-down" />
        </button>
      </span>
      {open && (
        <ul
          ref={list}
          id={listId}
          role="listbox"
          aria-label={label}
          data-cursor-region="popup"
          className="fixed z-10 my-xs list-none overflow-y-auto rounded-popup border border-line-strong bg-popup p-xs text-fg shadow-popup"
        >
          {options.map((option, index) => (
            <li
              key={`${index}-${option.value}`}
              id={`${id}-${index}`}
              role="option"
              aria-selected={index === active}
              aria-disabled={option.disabled}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => choose(option)}
              className={`rounded-control px-m py-s ${option.disabled ? "text-disabled" : "cursor-default"} ${index === active && !option.disabled ? "bg-hover" : ""}`}
            >
              {option.label}
            </li>
          ))}
        </ul>
      )}
    </FormRow>
  );
}
