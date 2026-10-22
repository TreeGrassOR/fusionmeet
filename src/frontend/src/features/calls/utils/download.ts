/**
 * Save a text made in the browser as a file.
 */
export const downloadTextFile = (
  content: string,
  fileName: string,
  type: string
) => {
  const url = URL.createObjectURL(new Blob([content], { type }))
  const link = document.createElement('a')
  link.href = url
  link.download = fileName
  document.body.appendChild(link)
  link.click()
  link.remove()
  // let the browser start the download before freeing the file
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
