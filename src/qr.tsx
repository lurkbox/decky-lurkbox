import qrcode from "qrcode-generator";

/** A QR code as an inline SVG (dark cells on a white tile, so it scans on any theme). */
export function QrCode({ text, size }: { text: string; size: number }) {
  let svg = "";
  try {
    const qr = qrcode(0, "M");
    qr.addData(text);
    qr.make();
    svg = qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true });
  } catch {
    return null;
  }
  return (
    <div
      style={{ width: size, height: size, margin: "6px auto", background: "#fff", padding: 4, borderRadius: 6 }}
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
