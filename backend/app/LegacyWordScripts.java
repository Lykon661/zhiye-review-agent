// Read original DOC run properties that Tika's XHTML formatter does not emit.
// Uses the Apache POI/Jackson classes already bundled in tika-app; no new jars.
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.poi.hwpf.HWPFDocument;
import org.apache.poi.hwpf.OldWordFileFormatException;
import org.apache.poi.hwpf.usermodel.CharacterRun;
import org.apache.poi.hwpf.usermodel.Paragraph;
import org.apache.poi.hwpf.usermodel.Range;

class LegacyWordScripts {
    private static String clean(String text) {
        // Each record is already a paragraph. Keep soft line breaks but exclude
        // its terminating CR, which could otherwise land inside a closing script.
        return text.replace("\r", "").replace('\u000b', '\n')
            .replace('\u001e', '\u2011').replace('\u001f', '\u200b')
            .replaceAll("[\\x00-\\x08\\x0c\\x0e-\\x1d]", "");
    }

    private static String escape(String text) {
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;");
    }

    public static void main(String[] args) throws Exception {
        List<Map<String, String>> paragraphs = new ArrayList<>();
        try (InputStream stream = Files.newInputStream(Path.of(args[0]));
             HWPFDocument document = new HWPFDocument(stream)) {
            Range range = document.getRange();
            for (int index = 0; index < range.numParagraphs(); index++) {
                Paragraph paragraph = range.getParagraph(index);
                StringBuilder plain = new StringBuilder();
                StringBuilder formatted = new StringBuilder();
                boolean containsFields = false;
                String activeTag = null;
                for (int runIndex = 0; runIndex < paragraph.numCharacterRuns(); runIndex++) {
                    CharacterRun run = paragraph.getCharacterRun(runIndex);
                    if (run.isVanished() || run.isMarkedDeleted()) continue;
                    String raw = run.text();
                    // Leave field substitution to Tika; don't associate a field's
                    // instruction text with its rendered result by deleting controls.
                    containsFields |= raw.indexOf('\u0013') >= 0 || raw.indexOf('\u0014') >= 0 || raw.indexOf('\u0015') >= 0;
                    String text = clean(raw);
                    if (text.isEmpty()) continue;
                    int script = run.getSubSuperScriptIndex();
                    int offset = run.getVerticalOffset();
                    String tag = script == 1 ? "sup" : script == 2 ? "sub"
                        : offset > 0 ? "sup" : offset < 0 ? "sub" : null;
                    // Merge adjacent runs sharing a script, even if their font or
                    // emphasis differs, so a multi-digit exponent stays together.
                    if (!java.util.Objects.equals(tag, activeTag)) {
                        if (activeTag != null) formatted.append("</").append(activeTag).append(">");
                        if (tag != null) formatted.append("<").append(tag).append(">");
                        activeTag = tag;
                    }
                    plain.append(text);
                    formatted.append(tag == null ? text : escape(text));
                }
                if (activeTag != null) formatted.append("</").append(activeTag).append(">");
                if (!containsFields && plain.length() > 0) {
                    Map<String, String> value = new LinkedHashMap<>();
                    value.put("plain", plain.toString());
                    value.put("formatted", formatted.toString());
                    paragraphs.add(value);
                }
            }
        } catch (OldWordFileFormatException unsupported) {
            // Word 6/95 remains handled by Tika's original parser.
        }
        new ObjectMapper().writeValue(System.out, paragraphs);
    }
}
