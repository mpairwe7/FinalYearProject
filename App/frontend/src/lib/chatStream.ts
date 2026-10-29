/** Bounded SSE reader for the chat POST transport. EOF is not a completed answer. */
export async function consumeChatStream(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  dispatch: (event: string, lines: string[]) => void,
): Promise<void> {
  const decoder = new TextDecoder();
  let line = '', event = 'token', data: string[] = [];
  let skipLf = false, completed = false, eventSize = 0, totalSize = 0;
  const processLine = () => {
    if (!line) {
      if (data.length) {
        if (event === 'error') throw new Error('Chat stream failed');
        dispatch(event, data);
        if (event === 'done') completed = true;
      }
      event = 'token';
      data = [];
      eventSize = 0;
    } else if (!line.startsWith(':')) {
      const colon = line.indexOf(':');
      const field = colon < 0 ? line : line.slice(0, colon);
      let value = colon < 0 ? '' : line.slice(colon + 1);
      if (value.startsWith(' ')) value = value.slice(1);
      if (field === 'event') event = value || 'token';
      if (field === 'data') data.push(value);
    }
    line = '';
  };
  const feed = (text: string) => {
    for (const char of text) {
      if (completed) break;
      totalSize++;
      eventSize++;
      if (eventSize > 262_144 || totalSize > 4_194_304) throw new Error('Chat stream exceeds limit');
      if (skipLf && char === '\n') { skipLf = false; continue; }
      skipLf = char === '\r';
      if (char === '\r' || char === '\n') processLine();
      else line += char;
    }
  };
  try {
    while (!completed) {
      const { done, value } = await reader.read();
      if (done) { feed(decoder.decode()); break; }
      feed(decoder.decode(value, { stream: true }));
    }
    // A final event without its blank-line delimiter is incomplete per SSE.
    if (!completed) throw new Error('Chat stream ended before done');
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
