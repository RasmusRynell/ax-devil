import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { useSelectOnOpen } from "./Fields";
import { Icon } from "./Icon";
import { FIELD, FIELD_IN } from "./look";
import { ownKeys } from "./keys";

/** Rows of a label and its control: one label column as wide as the longest label, the controls filling the rest. */
const GRID =
  "grid min-w-0 grid-cols-[max-content_minmax(0,1fr)] gap-x-l gap-y-s @max-sm:grid-cols-[minmax(0,1fr)]";

/**
 * A form of labelled rows. The labels sit in one column left of the controls; in a container too narrow for both,
 * each label sits over its control. Rows are `FormInput`, `FormSelect`, `FormCombo` and `FormNote`.
 */
export function Form({ children }: { children: ReactNode }) {
  return (
    <div className="@container min-w-0">
      <div className={GRID}>{children}</div>
    </div>
  );
}

/** A line of text across the whole form, such as the description of the choice above it. */
export function FormNote({ children }: { children: ReactNode }) {
  return <p className="col-span-full m-0 min-w-0 [overflow-wrap:anywhere]">{children}</p>;
}

/**
 * Pages of rows that share the form's columns, so their labels line up with the rows around them. Only the `current`
 * page shows, and the stack is as tall as its tallest page, so the form keeps its size from page to page.
 */
export function FormStack({
  current,
  pages,
}: {
  current: string;
  pages: Record<string, ReactNode>;
}) {
  return (
    <div className="col-span-full grid grid-cols-subgrid">
      {Object.entries(pages).map(([name, rows]) => (
        <div
          key={name}
          className={`col-span-full row-start-1 grid grid-cols-subgrid content-start gap-y-s ${name === current ? "" : "invisible"}`}
        >
          {rows}
        </div>
      ))}
    </div>
  );
}

interface RowProps {
  label: string;
  /** The id of the control, which the label names. */
  id: string;
  /** The control, then anything that sits after it in the row. */
  children: ReactNode;
  /** A line under the control, such as a question about it. */
  note?: ReactNode;
}

function Row({ label, id, children, note }: RowProps) {
  return (
    <>
      <label htmlFor={id} className="flex min-h-row min-w-0 items-center [overflow-wrap:anywhere]">
        {label}
      </label>
      <div className="flex min-w-0 flex-col gap-xs">
        <div className="flex min-w-0 items-center gap-s">{children}</div>
        {note}
      </div>
    </>
  );
}

interface Common {
  label: string;
  /** Sits after the control. */
  extra?: ReactNode;
  note?: ReactNode;
  disabled?: boolean;
  /** Shown as a tooltip on the control. */
  title?: string;
}

/** A labelled text box that fills its row; `masked` hides what is typed. */
export function FormInput({
  label,
  value,
  onChange,
  placeholder,
  masked = false,
  selectOnOpen = false,
  extra,
  note,
  disabled,
  title,
}: Common & {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  masked?: boolean;
  /** Takes the focus with its text selected once it is on screen, as an edit of this value starts. */
  selectOnOpen?: boolean;
}) {
  const id = useId();
  const input = useSelectOnOpen(selectOnOpen);
  return (
    <Row label={label} id={id} note={note}>
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
        onChange={(event) => onChange(event.target.value)}
        className={`${FIELD} min-w-0 flex-1`}
      />
      {extra}
    </Row>
  );
}

/** A labelled drop-down over a closed set; `failed` marks the chosen status line as an error. */
export function FormSelect<T extends string>({
  label,
  value,
  options,
  onChange,
  failed = false,
  extra,
  note,
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
    <Row label={label} id={id} note={note}>
      <span className="relative flex min-w-0 flex-1 items-center">
        {failed && (
          <Icon name="triangle-alert" className="pointer-events-none absolute left-s text-danger" />
        )}
        <select
          id={id}
          value={value}
          disabled={disabled}
          title={title}
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
    </Row>
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
  value,
  onChange,
  placeholder,
  options,
  onOpen,
  note,
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
    <Row label={label} id={id} note={note}>
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
    </Row>
  );
}
