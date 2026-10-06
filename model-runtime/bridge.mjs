// One request per process. Secrets arrive on stdin, never in command-line arguments.

const send = value => process.stdout.write(`${JSON.stringify(value)}\n`);
const allowedProviders = new Set([
  'openai', 'anthropic', 'google', 'deepseek', 'openrouter', 'groq', 'xai',
  'mistral', 'moonshotai', 'moonshotai-cn', 'zai', 'zai-coding-cn',
  'minimax', 'minimax-cn', 'together', 'nvidia', 'kimi-coding',
]);
const apiFactories = {
  'openai-completions': ['openai-completions', 'openAICompletionsApi'],
  'openai-responses': ['openai-responses', 'openAIResponsesApi'],
  'anthropic-messages': ['anthropic-messages', 'anthropicMessagesApi'],
  'google-generative-ai': ['google-generative-ai', 'googleGenerativeAIApi'],
};

function context(messages, model) {
  return { messages: messages.map(message => {
    const timestamp = Date.now();
    if (message.role !== 'assistant') return { ...message, timestamp };
    return {
      role: 'assistant', content: [{ type: 'text', text: message.content }],
      api: model.api, provider: model.provider, model: model.id, stopReason: 'stop', timestamp,
      usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
    };
  }) };
}

async function main() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const request = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  let sdk, builtinModels;
  try {
    sdk = await import('@earendil-works/pi-ai');
    ({ builtinModels } = await import('@earendil-works/pi-ai/providers/all'));
  } catch {
    send({ type: 'error', message: '模型运行依赖未安装。请在项目目录执行 npm.cmd install --prefix model-runtime。' });
    return;
  }
  const builtins = builtinModels();
  if (request.action === 'catalog') {
    send({ type: 'catalog', providers: builtins.getProviders()
      .filter(provider => allowedProviders.has(provider.id))
      .map(provider => ({ id: provider.id, name: provider.name || provider.id,
        models: builtins.getModels(provider.id).filter(model => apiFactories[model.api]).map(model => ({ id: model.id, name: model.name,
          api: model.api, base_url: model.baseUrl, context_window: model.contextWindow,
          max_tokens: model.maxTokens, reasoning: model.reasoning,
          supports_images: model.input.includes('image') })) })).filter(provider => provider.models.length) });
    return;
  }
  const config = request.config;
  const known = config.provider === 'custom' ? undefined : builtins.getModel(config.provider, config.model);
  const api = config.api || known?.api;
  const factory = apiFactories[api];
  if (!factory) throw new Error('当前页面不支持该接口协议，请选择其他模型或自定义接口。');
  const hasImages = request.messages.some(message => Array.isArray(message.content)
    && message.content.some(block => block.type === 'image'));
  const supportsImages = known ? known.input.includes('image') : config.supports_images === true;
  if (hasImages && !supportsImages) {
    send({ type: 'error', code: 'image_not_supported' });
    return;
  }
  const module = await import(`@earendil-works/pi-ai/api/${factory[0]}.lazy`);
  const model = {
    ...(known || {}), id: config.model, name: known?.name || config.model,
    api, provider: config.provider, baseUrl: config.base_url || known?.baseUrl,
    reasoning: config.reasoning, input: supportsImages ? ['text', 'image'] : ['text'],
    contextWindow: config.context_window, maxTokens: config.max_tokens,
    cost: known?.cost || { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    ...(api === 'openai-completions' ? { compat: { ...known?.compat,
      supportsDeveloperRole: false, supportsStore: false, supportsUsageInStreaming: false,
      ...(config.provider === 'custom' ? { maxTokensField: 'max_tokens' } : {}) } } : {}),
  };
  // An explicit provider prevents inherited environment keys from overriding UI credentials.
  const models = sdk.createModels();
  models.setProvider(sdk.createProvider({ id: model.provider, name: model.provider,
    baseUrl: model.baseUrl, models: [model], api: module[factory[1]](),
    auth: { apiKey: { name: model.provider, resolve: async () => ({ auth: config.api_key
      ? { apiKey: config.api_key } : {} }) } },
  }));
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), config.timeout_seconds * 1000);
  let status;
  const options = {
    apiKey: config.api_key || (api.startsWith('openai-') ? 'local-no-key' : undefined),
    signal: controller.signal, timeoutMs: config.timeout_seconds * 1000, maxRetries: 0,
    maxTokens: Math.min(request.max_tokens || config.max_tokens, config.max_tokens),
    ...(!config.reasoning ? { temperature: 0.15 } : {}),
    onResponse: response => { status = response.status; },
    onPayload: payload => {
      if (request.json_mode && api === 'openai-completions') payload.response_format = { type: 'json_object' };
      if (new URL(model.baseUrl).hostname === 'api.deepseek.com' && !config.reasoning)
        payload.thinking = { type: 'disabled' };
      return payload;
    },
  };
  const input = context(request.messages, model);
  const imageErrorCode = message => hasImages
    && /image|vision|multimodal|图片|图像/i.test(message || '')
    && /unsupported|not support|does not support|text.only|only text|不支持/i.test(message || '')
      ? 'image_not_supported' : undefined;
  if (request.json_mode) input.messages.unshift({ role: 'system',
    content: '请仅输出一个完整的 JSON 对象，不要使用 Markdown 代码块。', timestamp: Date.now() });
  try {
    let response;
    if (request.action === 'stream') {
      const stream = models.stream(model, input, options);
      for await (const event of stream) {
        if (event.type === 'text_delta') send({ type: 'delta', text: event.delta });
      }
      response = await stream.result();
    } else {
      response = await models.complete(model, input, options);
      // Gateways may reject JSON response_format; retry only this unsupported parameter.
      if (request.json_mode && api === 'openai-completions' && response.stopReason === 'error'
        && /response_format|json_object/i.test(response.errorMessage || '')
        && /unsupported|not supported|unknown|unrecognized|unexpected|invalid parameter|不支持/i.test(response.errorMessage || '')) {
        request.json_mode = false;
        response = await models.complete(model, input, options);
      }
    }
    if (response.stopReason === 'error' || response.stopReason === 'aborted') {
      send({ type: 'error', code: imageErrorCode(response.errorMessage), status, timeout: controller.signal.aborted,
        message: response.errorMessage || '模型请求失败。' });
      return;
    }
    const text = response.content.filter(block => block.type === 'text').map(block => block.text).join('');
    send({ type: 'done', text, stop_reason: response.stopReason });
  } catch (error) {
    send({ type: 'error', code: imageErrorCode(error.message), status: error.status || status, timeout: controller.signal.aborted,
      message: error.message || '模型请求失败。' });
  } finally { clearTimeout(timer); }
}

main().catch(error => send({ type: 'error', message: error.message || '模型接入失败。' }));
