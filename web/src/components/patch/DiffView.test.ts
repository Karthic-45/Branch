import { describe, expect, it } from "vitest";
import { parseUnifiedDiff } from "./DiffView";

/** A real `git diff` against two files, including a rename-free edit and a
 * pure addition, so line numbering and both counters are exercised. */
const REAL = `diff --git a/src/sample/simple.py b/src/sample/simple.py
index 83db48f..bf269f4 100644
--- a/src/sample/simple.py
+++ b/src/sample/simple.py
@@ -1,4 +1,5 @@
 # src/sample/simple.py
 def add_one(number):
-    return number + 1
+    """Add one."""
+    return number + 1
diff --git a/src/sample/asgi.py b/src/sample/asgi.py
new file mode 100644
index 0000000..2a1b3c4
--- /dev/null
+++ b/src/sample/asgi.py
@@ -0,0 +1,2 @@
+from asgiref.wsgi import WsgiToAsgi
+application = WsgiToAsgi(app)
`;

describe("parseUnifiedDiff", () => {
  it("splits a multi-file patch and counts each file separately", () => {
    const files = parseUnifiedDiff(REAL);
    expect(files.map((f) => f.path)).toEqual(["src/sample/simple.py", "src/sample/asgi.py"]);
    expect(files[0].added).toBe(2);
    expect(files[0].removed).toBe(1);
    expect(files[1].added).toBe(2);
    expect(files[1].removed).toBe(0);
  });

  it("numbers old and new lines from the hunk header", () => {
    const [first] = parseUnifiedDiff(REAL);
    const lines = first.hunks[0].lines;
    expect(lines[0]).toMatchObject({ kind: "ctx", oldNo: 1, newNo: 1 });
    const removed = lines.find((l) => l.kind === "del")!;
    expect(removed).toMatchObject({ oldNo: 3, newNo: null });
    const added = lines.filter((l) => l.kind === "add");
    // An added line advances only the new-side counter.
    expect(added.every((l) => l.oldNo === null)).toBe(true);
    expect(added.map((l) => l.newNo)).toEqual([3, 4]);
  });

  it("strips the leading marker from line text", () => {
    const [first] = parseUnifiedDiff(REAL);
    const added = first.hunks[0].lines.find((l) => l.kind === "add")!;
    expect(added.text).toBe('    """Add one."""');
  });

  it("rejects an artifact that is not a diff, rather than inventing a file", () => {
    // Exactly what the stand-in explorer writes today. It starts with "--- ",
    // which is enough to look like a file header -- the panel used to report
    // it as "1 file changed, +0 -0" with the placeholder text as the filename.
    const placeholder = "--- fake patch for: Wrap the existing Flask app in an ASGI shim ---\n";
    expect(parseUnifiedDiff(placeholder)).toEqual([]);
  });

  it("returns nothing for an empty artifact", () => {
    expect(parseUnifiedDiff("")).toEqual([]);
  });

  it("keeps a binary file even though it has no hunks", () => {
    const binary = [
      "diff --git a/logo.png b/logo.png",
      "index 1234567..89abcde 100644",
      "Binary files a/logo.png and b/logo.png differ",
      "",
    ].join("\n");
    const [file] = parseUnifiedDiff(binary);
    expect(file.path).toBe("logo.png");
    expect(file.binary).toBe(true);
  });

  it("handles a bare diff with no `diff --git` header", () => {
    const bare = ["--- a/x.py", "+++ b/x.py", "@@ -1 +1 @@", "-old", "+new", ""].join("\n");
    const [file] = parseUnifiedDiff(bare);
    expect(file.path).toBe("x.py");
    expect(file.added).toBe(1);
    expect(file.removed).toBe(1);
  });
});
