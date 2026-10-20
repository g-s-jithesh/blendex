import { useId, useState, type KeyboardEvent } from "react";

export type ComboOption = { value: string; label: string };

type Props = {
  label: string;
  allLabel: string;
  value: string;
  options: ComboOption[];
  onChange: (value: string) => void;
  freeText?: boolean;
  className?: string;
};

export function Combobox({ label, allLabel, value, options, onChange, freeText = false, className = "w-28" }: Props) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState<string | null>(null);
  const [highlight, setHighlight] = useState(0);

  const needle = (query ?? "").toLowerCase();
  const shown = needle
    ? options.filter((o) => o.value.toLowerCase().includes(needle))
    : [{ value: "", label: allLabel }, ...options];
  const expanded = open && shown.length > 0;
  const active = expanded && highlight >= 0 ? shown[Math.min(highlight, shown.length - 1)] : undefined;
  const optionId = (i: number) => `${id}-${i}`;

  const show = (input: HTMLInputElement) => {
    input.select();
    if (open) return;
    setOpen(true);
    setQuery(null);
    const current = options.findIndex((o) => o.value === value);
    setHighlight(value && current < 0 ? -1 : current + 1);
  };
  const close = () => {
    setOpen(false);
    setQuery(null);
  };
  const pick = (v: string) => {
    onChange(v);
    close();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.nativeEvent.isComposing) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!open) return show(e.currentTarget);
      const step = e.key === "ArrowDown" ? 1 : -1;
      const n = shown.length;
      setHighlight((h) => (h < 0 ? (step > 0 ? 0 : n - 1) : (Math.min(h, n - 1) + step + n) % n));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (active) pick(active.value);
      else close();
    } else if (e.key === "Escape") {
      if (open) close();
      else pick("");
    }
  };

  return (
    <div className="relative">
      <input
        role="combobox"
        aria-label={label}
        aria-expanded={expanded}
        aria-controls={`${id}-list`}
        aria-autocomplete="list"
        aria-activedescendant={active ? optionId(shown.indexOf(active)) : undefined}
        placeholder={allLabel}
        className={`${className} rounded bg-neutral-800 py-1 pl-2 ${value ? "pr-6" : "pr-2"}`}
        value={query ?? value}
        onFocus={(e) => show(e.currentTarget)}
        onClick={(e) => show(e.currentTarget)}
        onBlur={close}
        onKeyDown={onKeyDown}
        onChange={(e) => {
          setOpen(true);
          setQuery(e.target.value);
          setHighlight(0);
          if (freeText) onChange(e.target.value);
        }}
      />
      {value && (
        <button
          type="button"
          aria-label={`Clear ${label.toLowerCase()} filter`}
          className="absolute right-1 top-1/2 -translate-y-1/2 px-1 text-neutral-400 hover:text-neutral-100"
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => pick("")}
        >
          ×
        </button>
      )}
      <ul
        id={`${id}-list`}
        role="listbox"
        aria-label={label}
        hidden={!expanded}
        className="absolute left-0 top-full z-50 mt-1 max-h-72 min-w-full overflow-auto rounded bg-neutral-800 py-1 shadow-lg ring-1 ring-neutral-700"
      >
        {shown.map((o, i) => (
          <li
            key={o.value}
            id={optionId(i)}
            role="option"
            aria-selected={o === active}
            className={`cursor-pointer whitespace-nowrap px-2 py-0.5 ${o === active ? "bg-sky-600 text-white" : ""} ${o.value ? "" : "text-neutral-400"}`}
            onMouseDown={(e) => e.preventDefault()}
            onMouseEnter={() => setHighlight(i)}
            onClick={() => pick(o.value)}
          >
            {o.label}
          </li>
        ))}
      </ul>
    </div>
  );
}
