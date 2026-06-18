interface Props {
  name: string;
  narrative: string;
  onName: (v: string) => void;
  onNarrative: (v: string) => void;
}

/** The two thesis fields — name + narrative — shared by the create form (M1a) and, next, the
 *  narrative editor (M1b). Presentational only: it owns no state and writes nothing; the parent holds
 *  the values and decides what to do on submit. The narrative is the spine of everything downstream
 *  (it's what "Draft from narrative" reads), so it gets the room of a textarea. */
export function ThesisFields({ name, narrative, onName, onNarrative }: Props) {
  return (
    <div className="wb-fields">
      <label className="wb-field">
        <span>Name</span>
        <input
          className="wb-input"
          value={name}
          onChange={(e) => onName(e.target.value)}
          placeholder="e.g. Small modular nuclear"
          aria-label="thesis name"
        />
      </label>
      <label className="wb-field">
        <span>
          Narrative <em>— your words, preserved</em>
        </span>
        <textarea
          className="wb-textarea"
          value={narrative}
          onChange={(e) => onNarrative(e.target.value)}
          rows={5}
          placeholder="The story, the conviction, the catalyst — why this, why now."
          aria-label="thesis narrative"
        />
      </label>
    </div>
  );
}
