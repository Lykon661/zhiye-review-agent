// Keep in sync with backend/app/upload_formats.py for both upload entry points.
export const uploadAccept = '.pdf,.doc,.docx,.rtf,.ppt,.pptx,.txt,.md,.markdown,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff'
export const uploadFormatsLabel = '支持 PDF、Word（DOC/DOCX/RTF）、PPT（PPT/PPTX）、TXT、Markdown、图片（PNG/JPG/JPEG/WEBP/BMP/TIF/TIFF）'

const uploadSuffixes = new Set(uploadAccept.split(','))
export function supportsUpload(filename: string): boolean {
  const dot = filename.lastIndexOf('.')
  return dot >= 0 && uploadSuffixes.has(filename.slice(dot).toLowerCase())
}
