import type { Ast } from "./ast";
import type { Diagnostics, Source, Span } from "./diagnostic";
import { type Token, tokenize } from "./tokenize";

const COMPARE = new Set(["==", "!=", "<", "<=", ">", ">="]);

class Parser {
  private at = 0;

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

  parse(): Ast {
    const ast = this.or();
    if (this.peek.kind !== "end")
      this.diag.fail("syntax", this.src, this.peek.span, "unexpected text after the expression");
    return ast;
  }

  private binary(op: string, left: Ast, right: Ast): Ast {
    return { kind: "binary", op, left, right, span: this.join(left.span, right.span) };
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
    const arg = this.not();
    return { kind: "unary", op: "!", arg, span: this.join(bang.span, arg.span) };
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
      return { kind: "in", left, list, span: this.join(left.span, list.span) };
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
    const arg = this.unary();
    const span = this.join(minus.span, arg.span);
    // A minus in front of a number is the negative number, so constants stay constants.
    if (arg.kind === "num") return { kind: "num", value: -arg.value, px: arg.px, span };
    return { kind: "unary", op: "-", arg, span };
  }

  private primary(): Ast {
    const token = this.take();
    switch (token.kind) {
      case "num":
        return { kind: "num", value: token.value, px: token.px, span: token.span };
      case "str":
        return { kind: "str", value: token.text, span: token.span };
      case "color":
        return { kind: "color", value: token.value, span: token.span };
      case "ident":
        return this.word(token);
      case "op":
        if (token.text === "(") {
          const inner = this.or();
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
    if (!this.isOp("]")) {
      items.push(this.or());
      while (this.isOp(",")) {
        this.take();
        items.push(this.or());
      }
    }
    const close = this.expectOp("]");
    return { kind: "list", items, span: this.join(open.span, close.span) };
  }

  private word(token: Token): Ast {
    const name = token.text;
    if (name === "null" || name === "true" || name === "false")
      return { kind: "lit", value: name, span: token.span };
    if (this.isOp("(")) {
      this.take();
      const args: Ast[] = [];
      if (!this.isOp(")")) {
        args.push(this.or());
        while (this.isOp(",")) {
          this.take();
          args.push(this.or());
        }
      }
      const close = this.expectOp(")");
      return { kind: "call", name, args, span: this.join(token.span, close.span) };
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
    return { kind: "path", segments, span: this.join(token.span, end) };
  }
}

/** Parse an expression; a syntax problem is recorded and null returned. */
export function parseExpr(src: Source, diag: Diagnostics): Ast | null {
  return diag.attempt(() => new Parser(tokenize(src, diag), src, diag).parse());
}
