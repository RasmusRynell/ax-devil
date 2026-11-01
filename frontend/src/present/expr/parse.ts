import type { Ast } from "./ast";
import type { Diagnostics, Source, Span } from "./diagnostic";
import { type Token, tokenize } from "./tokenize";

const COMPARE = new Set(["==", "!=", "<", "<=", ">", ">="]);

/** The longest expression text compiled; a longer one is refused before it is read. */
export const MAX_EXPRESSION_LENGTH = 4096;
/** The deepest nesting of brackets, calls, prefix operators and operator chains an expression may have. */
export const MAX_DEPTH = 64;
/** The most syntax nodes one expression may have. */
export const MAX_NODES = 1024;

class Parser {
  private at = 0;
  private depth = 0;
  private nodes = 0;
  /** The height of each node, so a long chain of operators is bounded like a nest of brackets. */
  private readonly heights = new WeakMap<Ast, number>();

  constructor(
    private readonly tokens: readonly Token[],
    private readonly src: Source,
    private readonly diag: Diagnostics,
  ) {}

  private get peek(): Token {
    return this.tokens[this.at];
  }

  private isOp(text: string): boolean {
    return this.peek.kind === "op" && this.peek.text === text;
  }

  private isWord(text: string): boolean {
    return this.peek.kind === "ident" && this.peek.text === text;
  }

  private take(): Token {
    return this.tokens[this.at++];
  }

  private expectOp(text: string): Token {
    if (!this.isOp(text)) this.diag.fail("syntax", this.src, this.peek.span, `expected '${text}'`);
    return this.take();
  }

  private join(from: Span, to: Span): Span {
    return [from[0], to[1]];
  }

  /** Record a node of the tree, refusing one that makes the tree too big or too tall for the later passes to walk. */
  private node(ast: Ast, ...children: readonly Ast[]): Ast {
    if (++this.nodes > MAX_NODES)
      this.diag.fail("limit", this.src, ast.span, `more than ${MAX_NODES} parts in one expression`);
    const height = 1 + Math.max(0, ...children.map((child) => this.heights.get(child) ?? 0));
    if (height > MAX_DEPTH)
      this.diag.fail("limit", this.src, ast.span, `nested deeper than ${MAX_DEPTH} levels`);
    this.heights.set(ast, height);
    return ast;
  }

  /** Parse a part inside another, refusing nesting the recursion cannot hold. */
  private nest<T>(at: Span, parse: () => T): T {
    if (++this.depth > MAX_DEPTH)
      this.diag.fail("limit", this.src, at, `nested deeper than ${MAX_DEPTH} levels`);
    try {
      return parse();
    } finally {
      this.depth--;
    }
  }

  parse(): Ast {
    const ast = this.or();
    if (this.peek.kind !== "end")
      this.diag.fail("syntax", this.src, this.peek.span, "unexpected text after the expression");
    return ast;
  }

  private binary(op: string, left: Ast, right: Ast): Ast {
    return this.node(
      { kind: "binary", op, left, right, span: this.join(left.span, right.span) },
      left,
      right,
    );
  }

  private or(): Ast {
    let left = this.and();
    while (this.isOp("||")) {
      this.take();
      left = this.binary("||", left, this.and());
    }
    return left;
  }

  private and(): Ast {
    let left = this.not();
    while (this.isOp("&&")) {
      this.take();
      left = this.binary("&&", left, this.not());
    }
    return left;
  }

  private not(): Ast {
    if (!this.isOp("!")) return this.compare();
    const bang = this.take();
    const arg = this.nest(bang.span, () => this.not());
    return this.node({ kind: "unary", op: "!", arg, span: this.join(bang.span, arg.span) }, arg);
  }

  private compare(): Ast {
    const left = this.coalesce();
    if (this.peek.kind === "op" && COMPARE.has(this.peek.text)) {
      const op = this.take().text;
      return this.binary(op, left, this.coalesce());
    }
    if (this.isWord("in")) {
      this.take();
      const list = this.primary();
      if (list.kind !== "list")
        this.diag.fail("syntax", this.src, list.span, "'in' takes a list like ['a', 'b']");
      return this.node(
        { kind: "in", left, list, span: this.join(left.span, list.span) },
        left,
        list,
      );
    }
    return left;
  }

  private coalesce(): Ast {
    let left = this.sum();
    while (this.isOp("??")) {
      this.take();
      left = this.binary("??", left, this.sum());
    }
    return left;
  }

  private sum(): Ast {
    let left = this.product();
    while (this.isOp("+") || this.isOp("-")) {
      const op = this.take().text;
      left = this.binary(op, left, this.product());
    }
    return left;
  }

  private product(): Ast {
    let left = this.unary();
    while (this.isOp("*") || this.isOp("/")) {
      const op = this.take().text;
      left = this.binary(op, left, this.unary());
    }
    return left;
  }

  private unary(): Ast {
    if (!this.isOp("-")) return this.primary();
    const minus = this.take();
    const arg = this.nest(minus.span, () => this.unary());
    const span = this.join(minus.span, arg.span);
    // A minus in front of a number is the negative number, so constants stay constants.
    if (arg.kind === "num") return this.node({ kind: "num", value: -arg.value, px: arg.px, span });
    return this.node({ kind: "unary", op: "-", arg, span }, arg);
  }

  private primary(): Ast {
    const token = this.take();
    switch (token.kind) {
      case "num":
        return this.node({ kind: "num", value: token.value, px: token.px, span: token.span });
      case "str":
        return this.node({ kind: "str", value: token.text, span: token.span });
      case "color":
        return this.node({ kind: "color", value: token.value, span: token.span });
      case "ident":
        return this.word(token);
      case "op":
        if (token.text === "(") {
          const inner = this.nest(token.span, () => this.or());
          this.expectOp(")");
          return inner;
        }
        if (token.text === "[") return this.list(token);
        break;
      default:
        break;
    }
    return this.diag.fail(
      "syntax",
      this.src,
      token.span,
      token.kind === "end" ? "the expression ends too soon" : "unexpected token",
    );
  }

  private list(open: Token): Ast {
    const items: Ast[] = [];
    this.nest(open.span, () => {
      if (!this.isOp("]")) {
        items.push(this.or());
        while (this.isOp(",")) {
          this.take();
          items.push(this.or());
        }
      }
    });
    const close = this.expectOp("]");
    return this.node({ kind: "list", items, span: this.join(open.span, close.span) }, ...items);
  }

  private word(token: Token): Ast {
    const name = token.text;
    if (name === "null" || name === "true" || name === "false")
      return this.node({ kind: "lit", value: name, span: token.span });
    if (this.isOp("(")) {
      this.take();
      const args: Ast[] = [];
      this.nest(token.span, () => {
        if (!this.isOp(")")) {
          args.push(this.or());
          while (this.isOp(",")) {
            this.take();
            args.push(this.or());
          }
        }
      });
      const close = this.expectOp(")");
      return this.node(
        { kind: "call", name, args, span: this.join(token.span, close.span) },
        ...args,
      );
    }
    const segments = [name];
    let end = token.span;
    for (;;) {
      if (this.isOp(".")) {
        this.take();
        const next = this.take();
        if (next.kind !== "ident")
          this.diag.fail("syntax", this.src, next.span, "expected a name after '.'");
        segments.push(next.text);
        end = next.span;
      } else if (this.isOp("[")) {
        this.take();
        const key = this.take();
        if (key.kind !== "str")
          this.diag.fail("syntax", this.src, key.span, "expected a quoted name inside [ ]");
        segments.push(key.text);
        end = this.expectOp("]").span;
      } else {
        break;
      }
    }
    return this.node({ kind: "path", segments, span: this.join(token.span, end) });
  }
}

/** Parse an expression; a syntax problem is recorded and null returned. */
export function parseExpr(src: Source, diag: Diagnostics): Ast | null {
  if (src.text.length > MAX_EXPRESSION_LENGTH) {
    diag.add(
      "limit",
      src,
      [MAX_EXPRESSION_LENGTH, src.text.length],
      `longer than ${MAX_EXPRESSION_LENGTH} characters`,
    );
    return null;
  }
  return diag.attempt(() => new Parser(tokenize(src, diag), src, diag).parse());
}
