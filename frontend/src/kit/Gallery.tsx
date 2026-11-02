import { useState, type ReactNode } from "react";
import { Banner } from "../shell/Banner";
import { BrowseButton, Button, MenuButton } from "./Button";
import { Checkbox, SearchField, Select, TextField } from "./Fields";
import { Chip, ChipRow, ChoiceChips, SegmentedControl, ToggleChip } from "./Chips";
import { IconButton } from "./IconButton";
import { FIELD, type Tone } from "./look";
import { RowList } from "./RowList";
import { Switch } from "./Switch";
import { Tabs } from "./Tabs";
import { ChoiceDialog } from "./ChoiceDialog";
import { Icon } from "./Icon";
import { ICONS, type IconName } from "./icons";
import type { MenuEntry } from "./Menu";
import { SectionCaption } from "./SectionCaption";
import { ViewPanel } from "./ViewPanel";

const LONG =
  "The overlay for this lane does not line up with the video's frames, so boxes may land one frame early or late.";
const TONES: readonly Tone[] = ["neutral", "success", "caution", "danger"];
const MODES = [
  ["frame", "Frame"],
  ["time", "Time"],
  ["both", "Both"],
] as const;
const SPEEDS = [
  ["half", "0.5x"],
  ["one", "1x"],
  ["two", "2x"],
] as const;
const MENU: readonly MenuEntry[] = [
  { kind: "action", id: "copy", label: "Copy" },
  { kind: "check", id: "pin", label: "Pin" },
  { kind: "separator" },
  { kind: "action", id: "remove", label: "Remove", disabled: true },
];
const TABS = ["General", "Display", "Network"] as const;
const MARKS = [
  "muted",
  "accent",
  "success",
  "caution",
  "danger",
  "live",
  "video",
  "playlist",
  "lane",
] as const;
const FILTERS = ["Boxes", "Tracks", "Labels", "Events", "Attributes", "Heatmap"];

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="flex min-w-0 flex-col gap-m">
      <h2 className="m-0 text-small font-semibold text-muted">{title}</h2>
      {children}
    </section>
  );
}

/** A box that is `ch` characters wide, to show how a component wraps when it has little room. */
function Narrow({ children }: { children: ReactNode }) {
  return <div className="w-[20ch] border border-dashed border-line-strong">{children}</div>;
}

/**
 * Every shared kit component in one page, for the screenshot harness; the app opens it at `#kit-gallery` and
 * nothing else links to it.
 */
export function Gallery() {
  const [mode, setMode] = useState<(typeof MODES)[number][0]>("frame");
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number][0]>("one");
  const [filters, setFilters] = useState<ReadonlySet<string>>(new Set(["Boxes", "Labels"]));
  const [open, setOpen] = useState(true);
  const [asking, setAsking] = useState(false);
  const [answer, setAnswer] = useState("none");
  const [text, setText] = useState("");
  const [folded, setFolded] = useState(true);
  const [on, setOn] = useState(true);
  const [tab, setTab] = useState<(typeof TABS)[number]>("General");
  return (
    <main className="fixed inset-0 grid grid-cols-3 content-start items-start gap-xl overflow-y-auto p-xl">
      <Section title="Buttons">
        <div className="flex flex-wrap items-center gap-m">
          <Button>Normal</Button>
          <Button type="submit">Default</Button>
          <Button icon="plus">With icon</Button>
          <Button disabled>Disabled</Button>
          <Button type="submit" disabled>
            Default disabled
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-m">
          <Button size="small">Small</Button>
          <Button size="small" icon="refresh-cw">
            Small icon
          </Button>
          <Button size="small" disabled>
            Small disabled
          </Button>
          <Button icon="refresh-cw" label="Refresh" />
          <Button icon="refresh-cw" label="Refresh, disabled" disabled />
          <IconButton icon="x" label="Quiet icon" />
        </div>
        <div className="flex flex-wrap items-center gap-m">
          <MenuButton label="Menu" entries={MENU} checked={new Set(["pin"])} onSelect={() => {}} />
          <MenuButton label="Disabled" entries={MENU} onSelect={() => {}} disabled />
          <BrowseButton what="video file" onBrowse={() => undefined} />
        </div>
        <Button onClick={() => setAsking(true)}>Open choice dialog</Button>
        <output aria-label="Last choice">{answer}</output>
      </Section>

      <Section title="Chips">
        <ChipRow label="Status">
          {TONES.map((tone) => (
            <Chip key={tone} tone={tone}>
              {tone}
            </Chip>
          ))}
        </ChipRow>
        <ChipRow label="Clickable">
          {TONES.map((tone) => (
            <Chip key={tone} tone={tone} onClick={() => undefined}>
              {tone}
            </Chip>
          ))}
        </ChipRow>
        <Narrow>
          <ChipRow label="Long chip">
            <Chip tone="caution" title={LONG}>
              {LONG}
            </Chip>
          </ChipRow>
        </Narrow>
        <ChoiceChips label="Speed" options={SPEEDS} value={speed} onChange={setSpeed} />
        <ChipRow label="Filters">
          {FILTERS.map((name) => (
            <ToggleChip
              key={name}
              checked={filters.has(name)}
              disabled={name === "Heatmap"}
              icon={name === "Tracks" ? "eye" : undefined}
              title={name === "Heatmap" ? "Not available" : undefined}
              onChange={(on) =>
                setFilters((now) => {
                  const next = new Set(now);
                  if (on) next.add(name);
                  else next.delete(name);
                  return next;
                })
              }
            >
              {name}
            </ToggleChip>
          ))}
          <ToggleChip checked disabled onChange={() => undefined}>
            Locked
          </ToggleChip>
        </ChipRow>
        <div className="w-[24ch]">
          <ChipRow label="Filters, narrow">
            {FILTERS.map((name) => (
              <ToggleChip key={name} checked={name.length % 2 === 0} onChange={() => undefined}>
                {name}
              </ToggleChip>
            ))}
          </ChipRow>
        </div>
        <SegmentedControl label="Mode" options={MODES} value={mode} onChange={setMode} />
        <div className="w-[16ch]">
          <SegmentedControl label="Mode, narrow" options={MODES} value={mode} onChange={setMode} />
        </div>
      </Section>

      <Section title="Fields">
        <div className="flex flex-wrap items-center gap-m">
          <input
            aria-label="Plain text"
            placeholder="Text"
            className={`${FIELD} w-field`}
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
          <input
            aria-label="Disabled text"
            disabled
            defaultValue="Disabled"
            className={`${FIELD} w-field`}
          />
          <Button>Apply</Button>
          <Button type="submit">Save</Button>
        </div>
        <div className="flex flex-wrap items-center gap-m">
          <SearchField placeholder="Search" />
          <Button size="small">Find</Button>
          <BrowseButton what="folder" onBrowse={() => undefined} />
        </div>
        <TextField
          label="Path"
          value={text}
          onChange={setText}
          action={<BrowseButton what="video file" onBrowse={() => undefined} />}
        />
        <Select label="Mode" value={mode} options={MODES} onChange={(value) => setMode(value)} />
        <Checkbox label="A checkbox" checked={open} onChange={setOpen} />
        <Switch label="A switch" checked={on} onChange={setOn} />
        <Narrow>
          <TextField label="A label that is long" value={text} onChange={setText} />
          <Select
            label="Another long label"
            value={mode}
            options={MODES}
            onChange={(value) => setMode(value)}
          />
          <Checkbox label="A checkbox label" checked={open} onChange={setOpen} />
        </Narrow>
      </Section>

      <Section title="Tabs">
        <div className="flex flex-col gap-m border border-line">
          <Tabs label="Sections" tabs={TABS} value={tab} onChange={setTab} panelId="gallery-tab" />
          <p id="gallery-tab" role="tabpanel" className="m-0 px-m pb-m">
            {tab} panel
          </p>
        </div>
      </Section>

      <Section title="Banner">
        <div className="flex flex-col border border-line">
          <Banner
            role="status"
            actions={
              <>
                <Button size="small">Review</Button>
                <Button size="small">Ignore</Button>
              </>
            }
            onDismiss={() => undefined}
          >
            {LONG}
          </Banner>
          <Banner role="alert" onDismiss={() => undefined}>
            Short alert
          </Banner>
          <Banner role="status">Short status</Banner>
        </div>
        <Narrow>
          <Banner
            role="status"
            actions={<Button size="small">Review</Button>}
            onDismiss={() => undefined}
          >
            {LONG}
          </Banner>
        </Narrow>
      </Section>

      <Section title="Captions">
        <div className="flex flex-col border border-line">
          <SectionCaption heading="Lanes" />
          <SectionCaption heading="Lanes" count={12} note="local" />
          <SectionCaption heading="Lanes" count={3} aside="metadata.json" />
          <SectionCaption
            heading="Lanes"
            count={3}
            aside="a very long aside that cannot fit beside the heading"
          />
          {MARKS.map((mark) => (
            <SectionCaption key={mark} heading={mark} mark={mark} />
          ))}
          <SectionCaption
            heading="Folded"
            count={4}
            fold={{ open: !folded, onToggle: () => setFolded(!folded) }}
          />
          <SectionCaption
            heading="Open"
            count={4}
            fold={{ open: true, onToggle: () => undefined }}
          />
        </div>
        <Narrow>
          <SectionCaption heading="Objects" count={128} aside="cam-1 and cam-2" />
        </Narrow>
        <Narrow>
          <RowList
            heading="Recent"
            rows={[
              { id: "a", label: "Front door.mp4", icon: "folder" },
              { id: "b", label: "Garage", disabled: true },
            ]}
            onSelect={() => undefined}
          />
        </Narrow>
      </Section>

      <Section title="View panel">
        <div className="flex h-[16em] border border-line">
          <ViewPanel
            title="A view with a title that is far too long to fit its header"
            action={<IconButton icon="refresh-cw" label="Refresh" />}
          >
            {Array.from({ length: 12 }, (_, index) => (
              <p key={index} className="m-0 px-m py-xs">
                Row {index + 1} with a name long enough that it needs to wrap at the panel edge
              </p>
            ))}
          </ViewPanel>
        </div>
        <div className="flex h-[8em] border border-line">
          <ViewPanel title="Short">
            <p className="m-0 px-m py-xs">One row at the top</p>
          </ViewPanel>
        </div>
      </Section>

      <Section title="Icons">
        <ul className="m-0 grid list-none grid-cols-3 gap-m p-0">
          {(Object.keys(ICONS) as IconName[]).map((name) => (
            <li key={name} className="flex min-w-0 items-center gap-s text-small">
              <Icon name={name} />
              <span className="truncate">{name}</span>
            </li>
          ))}
        </ul>
      </Section>
      {asking && (
        <ChoiceDialog
          title="Restart"
          message={`The update is ready. Restart now to use it, or later to finish what you are doing first.\n\n${LONG}`}
          choices={[
            { id: "now", label: "Restart now" },
            { id: "later", label: "Later" },
          ]}
          defaultId="later"
          onChoose={(id) => {
            setAnswer(id);
            setAsking(false);
          }}
        />
      )}
    </main>
  );
}
