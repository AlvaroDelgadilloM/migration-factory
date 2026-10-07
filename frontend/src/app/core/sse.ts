/** Server-Sent Events over fetch so the bearer token travels in a header (never in the URL).
 *  Reconnects with Last-Event-ID until the server sends `event: end` or the caller aborts. */
export interface SseMessage { id: number | null; event: string; data: any }

export function streamEvents(url: string, token: () => string | null, onMessage: (m: SseMessage) => void,
                             signal: AbortSignal, lastEventId?: number): Promise<void> {
  let last = lastEventId ?? 0;
  const run = async (): Promise<void> => {
    let ended = false;
    try {
      const headers: Record<string, string> = { Accept: 'text/event-stream' };
      const t = token();
      if (t) headers['Authorization'] = `Bearer ${t}`;
      if (last) headers['Last-Event-ID'] = String(last);
      const res = await fetch(url, { headers, signal });
      if (!res.ok || !res.body) throw new Error('HTTP ' + res.status);
      const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
      let buf = '';
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += value;
        let idx: number;
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const block = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const msg: SseMessage = { id: null, event: 'message', data: null };
          for (const line of block.split('\n')) {
            if (line.startsWith('id:')) msg.id = Number(line.slice(3).trim());
            else if (line.startsWith('event:')) msg.event = line.slice(6).trim();
            else if (line.startsWith('data:')) msg.data = JSON.parse(line.slice(5).trim());
          }
          if (msg.data === null) continue; // keep-alive comment
          if (msg.id) last = msg.id;
          onMessage(msg);
          if (msg.event === 'end') ended = true;
        }
      }
    } catch (e) {
      if (signal.aborted) return;
    }
    if (!ended && !signal.aborted) {
      await new Promise(r => setTimeout(r, 2000));
      return run();
    }
  };
  return run();
}
