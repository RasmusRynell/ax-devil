// Generated from scene::inspection::leaf by make; do not edit.

/**
 * The value of a field: a leaf of the entity's data. On the wire an integer is its decimal digits in a string, so
 * that it stays exact in a JavaScript number-only world, and `Int(3)` differs from `Float(3.0)`.
 */
export type LeafWire = { "kind": "null" } | { "kind": "bool", "value": boolean } | { "kind": "int", "value": string } | { "kind": "float", "value": number } | { "kind": "text", "value": string };
