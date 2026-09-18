package engine

import (
	"strconv"
	"strings"
)

// An encoder that produces byte-identical output to Python's json.dumps(..., ensure_ascii=False).
//
// encoding/json can't guarantee Plan JSON byte-identity because its key order (map), string escaping
// (U+2028, \b, etc.), and indentation rules differ from Python's. This handles only the values the
// engine's output needs (strings, ints, booleans, null, lists, ordered objects).

// JObj is an ordered JSON object.
type JObj struct {
	keys []string
	vals []any
}

func Obj() *JObj { return &JObj{} }

// Set behaves like a Python dict assignment (an existing key keeps its position).
func (o *JObj) Set(key string, val any) *JObj {
	for i, k := range o.keys {
		if k == key {
			o.vals[i] = val
			return o
		}
	}
	o.keys = append(o.keys, key)
	o.vals = append(o.vals, val)
	return o
}

// Dumps: indent < 0 uses json.dumps' default separators (", ", ": "); otherwise indent=N (",", ": " + newline).
func Dumps(v any, indent int) string {
	var b strings.Builder
	writeValue(&b, v, indent, 0)
	return b.String()
}

func writeValue(b *strings.Builder, v any, indent, level int) {
	switch x := v.(type) {
	case nil:
		b.WriteString("null")
	case bool:
		if x {
			b.WriteString("true")
		} else {
			b.WriteString("false")
		}
	case int:
		b.WriteString(strconv.Itoa(x))
	case string:
		writeString(b, x)
	case []string:
		items := make([]any, len(x))
		for i, s := range x {
			items[i] = s
		}
		writeList(b, items, indent, level)
	case [][]string:
		items := make([]any, len(x))
		for i, s := range x {
			items[i] = s
		}
		writeList(b, items, indent, level)
	case []any:
		writeList(b, x, indent, level)
	case *JObj:
		writeObj(b, x, indent, level)
	case OrderedLists:
		o := Obj()
		for i, k := range x.Keys {
			o.Set(k, x.Vals[i])
		}
		writeObj(b, o, indent, level)
	default:
		panic("pyjson: unsupported type")
	}
}

func newline(b *strings.Builder, indent, level int) {
	b.WriteByte('\n')
	b.WriteString(strings.Repeat(" ", indent*level))
}

func writeList(b *strings.Builder, items []any, indent, level int) {
	if len(items) == 0 {
		b.WriteString("[]")
		return
	}
	b.WriteByte('[')
	for i, item := range items {
		if i > 0 {
			if indent < 0 {
				b.WriteString(", ")
			} else {
				b.WriteByte(',')
			}
		}
		if indent >= 0 {
			newline(b, indent, level+1)
		}
		writeValue(b, item, indent, level+1)
	}
	if indent >= 0 {
		newline(b, indent, level)
	}
	b.WriteByte(']')
}

func writeObj(b *strings.Builder, o *JObj, indent, level int) {
	if len(o.keys) == 0 {
		b.WriteString("{}")
		return
	}
	b.WriteByte('{')
	for i, k := range o.keys {
		if i > 0 {
			if indent < 0 {
				b.WriteString(", ")
			} else {
				b.WriteByte(',')
			}
		}
		if indent >= 0 {
			newline(b, indent, level+1)
		}
		writeString(b, k)
		b.WriteString(": ")
		writeValue(b, o.vals[i], indent, level+1)
	}
	if indent >= 0 {
		newline(b, indent, level)
	}
	b.WriteByte('}')
}

// writeString: follows Python json's ensure_ascii=False rules. Escapes only ", \, and control
// characters (0x00-0x1f); everything else (including DEL, U+2028, etc.) is written as-is.
func writeString(b *strings.Builder, s string) {
	const hex = "0123456789abcdef"
	b.WriteByte('"')
	for i := 0; i < len(s); i++ {
		c := s[i]
		switch c {
		case '"':
			b.WriteString(`\"`)
		case '\\':
			b.WriteString(`\\`)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		case '\b':
			b.WriteString(`\b`)
		case '\f':
			b.WriteString(`\f`)
		default:
			if c < 0x20 {
				b.WriteString(`\u00`)
				b.WriteByte(hex[c>>4])
				b.WriteByte(hex[c&0xf])
			} else {
				b.WriteByte(c)
			}
		}
	}
	b.WriteByte('"')
}

func itoa(n int) string {
	return strconv.Itoa(n)
}
