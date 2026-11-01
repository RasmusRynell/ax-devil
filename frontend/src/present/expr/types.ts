/** The types of the expression language. */
export type ExprType =
  | "number"
  | "bool"
  | "string"
  | "text"
  | "color"
  | "colors"
  | "length"
  | "box"
  | "point";

/**
 * A type not yet known: `attr.<name>`, variables and `null` get one, and every use narrows it. `string` and `text`
 * join to `text`, because a string can be written wherever text is.
 */
export class TypeVar {
  private parent: TypeVar | null = null;
  private known: ExprType | null = null;

  private root(): TypeVar {
    return this.parent ? this.parent.root() : this;
  }

  /** The type once something has determined it, else null. */
  get kind(): ExprType | null {
    return this.root().known;
  }

  /** Narrow to `type`; false when the uses disagree. */
  bind(type: ExprType): boolean {
    const root = this.root();
    const joined = join(root.known, type);
    if (joined === undefined) return false;
    root.known = joined;
    return true;
  }

  /** Make this and `other` one type; false when what they already know disagrees. */
  merge(other: TypeVar): boolean {
    const a = this.root();
    const b = other.root();
    if (a === b) return true;
    const joined = join(a.known, b.known);
    if (joined === undefined) return false;
    b.parent = a;
    a.known = joined;
    return true;
  }
}

/** A type or something that will become one. */
export type Ty = ExprType | TypeVar;

/** The type a type or type variable stands for now; null while open. */
export function kindOf(ty: Ty): ExprType | null {
  return typeof ty === "string" ? ty : ty.kind;
}

/** The common type of two (null = unknown yet), or undefined when they cannot be one. */
export function join(a: ExprType | null, b: ExprType | null): ExprType | null | undefined {
  if (a === null) return b;
  if (b === null || a === b) return a;
  return (a === "string" && b === "text") || (a === "text" && b === "string") ? "text" : undefined;
}

/** Make two types one; false when they disagree. A concrete `string` is accepted where `text` is wanted. */
export function unify(a: Ty, b: Ty): boolean {
  if (typeof a !== "string" && typeof b !== "string") return a.merge(b);
  if (typeof a !== "string") return a.bind(b as ExprType);
  if (typeof b !== "string") return b.bind(a);
  return join(a, b) !== undefined;
}
