import { describe, expect, it, vi } from 'vitest';
import { consumeChatStream } from '../../lib/chatStream';

function readerFor(chunks: string[]) {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  }).getReader();
}

describe('chat stream completion contract', () => {
  it('preserves multiline tokens, split CRLF, compact fields, comments and empty done', async () => {
    const dispatch = vi.fn();
    await consumeChatStream(readerFor([
      ': heartbeat\r', '\nevent:token\r', '\ndata:Tax\r\ndata:18%\r', '\n\r',
      '\nevent:done\ndata:\n\n',
    ]), dispatch);
    expect(dispatch.mock.calls).toEqual([['token', ['Tax', '18%']], ['done', ['']]]);
  });

  it('decodes Unicode split across byte chunks', async () => {
    const bytes = new TextEncoder().encode('event:token\ndata:Omusolo ✓\n\nevent:done\ndata:\n\n');
    const dispatch = vi.fn();
    const stream = new ReadableStream<Uint8Array>({ start(controller) {
      for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
      controller.close();
    } });
    await consumeChatStream(stream.getReader(), dispatch);
    expect(dispatch).toHaveBeenCalledWith('token', ['Omusolo ✓']);
  });

  it.each([
    'event:token\ndata:Partial tax guidance\n\n',
    'event:done\ndata:',
    'event:token\ndata:Partial\n\nevent:error\ndata:internal details\n\n',
  ])('rejects incomplete or failed answers and releases the reader', async (wire) => {
    const reader = readerFor([wire]);
    await expect(consumeChatStream(reader, vi.fn())).rejects.toThrow();
    expect(() => reader.releaseLock()).not.toThrow();
  });

  it('limits frames even if a peer never sends a delimiter', async () => {
    await expect(consumeChatStream(readerFor(['data:' + 'x'.repeat(262_145)]), vi.fn()))
      .rejects.toThrow('exceeds limit');
  });

  it('cancels immediately at done and ignores later untrusted tokens', async () => {
    const dispatch = vi.fn();
    const cancel = vi.fn();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) { controller.enqueue(new TextEncoder().encode('event:done\ndata:\n\nevent:token\ndata:Ignore\n\n')); },
      cancel,
    });
    await consumeChatStream(stream.getReader(), dispatch);
    expect(dispatch.mock.calls).toEqual([['done', ['']]]);
    expect(cancel).toHaveBeenCalledOnce();
  });
});
