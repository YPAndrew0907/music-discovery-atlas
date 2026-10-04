import {PINS} from './pins.mjs';
import {createTokenizer} from './tokenizer.mjs';
export function normalizeProjected(data) {
  if (data.length !== PINS.dimensions) throw new Error('Expected a projected 512-dimensional output');
  let sum = 0;
  for (const x of data) { if (!Number.isFinite(x)) throw new Error('Non-finite model output'); sum += x * x; }
  if (!(sum > 0)) throw new Error('Cannot normalize zero output');
  const norm = Math.sqrt(sum);
  return Float32Array.from(data, x => x / norm);
}
export async function createEncoder(ort, modelBytes, tokenizerJson, {wasmPaths} = {}) {
  if (ort.env.versions.web !== PINS.runtime.version) throw new Error('Runtime version does not match the pin');
  ort.env.wasm.numThreads = 1;
  ort.env.wasm.proxy = false; // Already inside a dedicated worker; never spawn a proxy.
  if (wasmPaths) ort.env.wasm.wasmPaths = wasmPaths;
  const tokenize = createTokenizer(tokenizerJson);
  const session = await ort.InferenceSession.create(modelBytes, {
    executionProviders: ['wasm'], executionMode: 'sequential', graphOptimizationLevel: 'all',
    intraOpNumThreads: 1, interOpNumThreads: 1
  });
  if (JSON.stringify([...session.inputNames].sort()) !== JSON.stringify(['input_ids']) ||
      session.outputNames.length !== 1 || session.outputNames[0] !== 'text_embeds') {
    await session.release(); throw new Error('Model input/output contract mismatch');
  }
  let disposed = false;
  return {
    async encode(text) {
      if (disposed) throw new Error('Encoder is disposed');
      const tokens = tokenize(text);
      const tensor = ids => new ort.Tensor('int64', BigInt64Array.from(ids, BigInt), [1, ids.length]);
      // This exact export takes input_ids only. One unpadded sample makes the reference attention mask all ones.
      const inputs = {input_ids: tensor(tokens.inputIds)};
      let outputs;
      try {
        outputs = await session.run(inputs);
        const result = outputs.text_embeds;
        if (JSON.stringify(result.dims) !== '[1,512]') throw new Error('Projected tensor shape mismatch');
        return {text, vector: normalizeProjected(result.data), inputIds: tokens.inputIds,
          attentionMask: tokens.attentionMask, truncated: tokens.truncated,
          spaceId: PINS.spaceId, modelSha256: PINS.artifacts[0].sha256,
          tokenizerSha256: PINS.artifacts.find(a => a.name === 'tokenizer.json').sha256,
          mode: 'experimental-live-wasm', releaseReady: false};
      } finally {
        Object.values(inputs).forEach(t => t.dispose());
        Object.values(outputs ?? {}).forEach(t => t.dispose());
      }
    },
    async dispose() { if (!disposed) { disposed = true; await session.release(); } }
  };
}
