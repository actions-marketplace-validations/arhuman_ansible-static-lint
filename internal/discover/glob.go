package discover

import "strings"

// expandBraces expands `{a,b}` alternations into a flat list of patterns.
// Nested braces are not supported because the upstream kind table does not use them.
func expandBraces(pattern string) []string {
	open := strings.IndexByte(pattern, '{')
	if open < 0 {
		return []string{pattern}
	}
	closeIdx := strings.IndexByte(pattern[open:], '}')
	if closeIdx < 0 {
		return []string{pattern}
	}
	closeIdx += open
	prefix, suffix := pattern[:open], pattern[closeIdx+1:]
	var out []string
	for _, alt := range strings.Split(pattern[open+1:closeIdx], ",") {
		out = append(out, expandBraces(prefix+alt+suffix)...)
	}
	return out
}

// matchGlob reports whether a slash-separated path matches a shell-style
// pattern supporting `*` (within a segment), `**` (any number of segments)
// and `{a,b}` alternations.
//
// It expands and splits the pattern on every call. Callers matching a fixed
// set of patterns repeatedly should precompile with compileGlob instead.
func matchGlob(pattern, path string) bool {
	return compileGlob(pattern).match(path)
}

// compiledGlob is a pattern with its brace expansion and segment split already
// done: the whole of the per-call work that depends only on the pattern.
type compiledGlob [][]string

// compileGlob precomputes the alternations of pattern as segment lists.
func compileGlob(pattern string) compiledGlob {
	alts := expandBraces(pattern)
	out := make(compiledGlob, len(alts))
	for i, p := range alts {
		out[i] = strings.Split(p, "/")
	}
	return out
}

// match reports whether a slash-separated path matches any alternation.
func (g compiledGlob) match(path string) bool {
	seg := strings.Split(path, "/")
	for _, pat := range g {
		if matchSegments(pat, seg) {
			return true
		}
	}
	return false
}

func matchSegments(pat, seg []string) bool {
	if len(pat) == 0 {
		return len(seg) == 0
	}
	if pat[0] == "**" {
		for i := 0; i <= len(seg); i++ {
			if matchSegments(pat[1:], seg[i:]) {
				return true
			}
		}
		return false
	}
	if len(seg) == 0 {
		return false
	}
	if !matchSegment(pat[0], seg[0]) {
		return false
	}
	return matchSegments(pat[1:], seg[1:])
}

// matchSegment matches a single path segment against a pattern where `*`
// matches any run of characters.
func matchSegment(pat, s string) bool {
	if pat == "*" {
		return true
	}
	star := strings.IndexByte(pat, '*')
	if star < 0 {
		return pat == s
	}
	head, tail := pat[:star], pat[star+1:]
	if !strings.HasPrefix(s, head) {
		return false
	}
	rest := s[len(head):]
	for i := 0; i <= len(rest); i++ {
		if matchSegment(tail, rest[i:]) {
			return true
		}
	}
	return false
}
