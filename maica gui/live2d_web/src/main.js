import * as PIXI from 'pixi.js'

window.PIXI = PIXI

const canvas = document.getElementById('app')
let app = null
let bridge = null
let model = null
let state = null
let modelNaturalWidth = 1
let modelNaturalHeight = 1
let currentEmotion = 'neutral'
let emotionParameters = {}
let speaking = false
let mouthTarget = 0
let mouthCurrent = 0
let lastParameterUpdate = performance.now()

function status(value) {
  if (bridge)
    bridge.rendererStatus(String(value))
}

function fail(error) {
  const message = error instanceof Error ? error.message : String(error || 'Live2D renderer failed')
  if (bridge)
    bridge.rendererError(message.slice(0, 500))
}

function loadScript(url) {
  return new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = url
    script.onload = resolve
    script.onerror = () => reject(new Error('Cubism Core could not be loaded'))
    document.head.appendChild(script)
  })
}

function fitModel() {
  if (!model || !app)
    return
  const width = Math.max(1, app.renderer.width / app.renderer.resolution)
  const height = Math.max(1, app.renderer.height / app.renderer.resolution)
  const scale = Math.min(width / modelNaturalWidth, height / modelNaturalHeight) * 0.92
  model.scale.set(scale)
  model.anchor.set(0.5, 0.5)
  model.position.set(width / 2, height / 2 + modelNaturalHeight * scale * 0.04)
}

function setParameter(id, value) {
  const core = model?.internalModel?.coreModel
  if (!core)
    return
  try {
    core.setParameterValueById(id, Number(value) || 0)
  }
  catch {
    // Models may omit optional standard parameters.
  }
}

function availableExpressionNames() {
  const definitions = model?.internalModel?.motionManager?.expressionManager?.definitions
  if (!Array.isArray(definitions))
    return []
  return definitions.map((item, index) => ({
    index,
    name: String(item?.Name || item?.name || item?.File || ''),
  }))
}

async function applyEmotion(value) {
  currentEmotion = String(value || 'neutral').toLowerCase()
  const mapping = state?.avatarMapping?.emotions || {}
  const rule = mapping[currentEmotion] || mapping.neutral || { expressions: [], parameters: {} }
  emotionParameters = rule.parameters || {}
  const available = availableExpressionNames()
  for (const candidate of rule.expressions || []) {
    const wanted = String(candidate).toLowerCase()
    const found = available.find((entry) => {
      const actual = entry.name.toLowerCase()
      return actual === wanted || actual.split(/[\\/]/).pop()?.replace(/\.exp3\.json$/i, '') === wanted
    })
    if (!found)
      continue
    try {
      await model?.expression(found.index)
    }
    catch {
      // Missing or malformed expressions fall back to model-neutral rendering.
    }
    return
  }
}

async function applyAction(value) {
  const action = String(value || '').toLowerCase()
  const rule = state?.avatarMapping?.actions?.[action]
  if (!rule)
    return
  const definitions = model?.internalModel?.motionManager?.definitions || {}
  const groups = Object.keys(definitions)
  for (const candidate of rule.motions || []) {
    const group = groups.find((name) => name.toLowerCase() === String(candidate).toLowerCase())
    if (!group)
      continue
    try {
      await model?.motion(group, 0, 3)
      return
    }
    catch {
      // Try the next configured motion group.
    }
  }
  await applyEmotion(rule.fallback_emotion || currentEmotion)
}

function updatePerformanceParameters() {
  const now = performance.now()
  const elapsed = Math.max(1, Math.min(100, now - lastParameterUpdate))
  lastParameterUpdate = now
  const target = speaking ? mouthTarget : 0
  const tau = target > mouthCurrent
    ? Math.max(10, Number(state?.mouthAttackMs) || 60)
    : Math.max(10, Number(state?.mouthReleaseMs) || 120)
  const alpha = 1 - Math.exp(-elapsed / tau)
  mouthCurrent += (target - mouthCurrent) * alpha
  if (Math.abs(mouthCurrent - target) < 0.002)
    mouthCurrent = target
  setParameter('ParamMouthOpenY', mouthCurrent)
  for (const [parameter, value] of Object.entries(emotionParameters))
    setParameter(parameter, value)
}

function handleCommand(raw) {
  let command
  try {
    command = JSON.parse(raw)
  }
  catch {
    return
  }
  const kind = String(command?.kind || '')
  const payload = command?.payload || {}
  if (kind === 'mouth')
    mouthTarget = Math.max(0, Math.min(1, Number(payload.value) || 0))
  else if (kind === 'speaking') {
    speaking = Boolean(payload.value)
    if (!speaking)
      mouthTarget = 0
  }
  else if (kind === 'emotion')
    applyEmotion(payload.value).catch(fail)
  else if (kind === 'action')
    applyAction(payload.value).catch(fail)
  else if (kind === 'refresh')
    fitModel()
}

async function start(initialState) {
  state = initialState
  status('core.loading')
  await loadScript(state.coreUrl)
  if (!window.Live2DCubismCore)
    throw new Error('Cubism Core loaded without Live2DCubismCore')
  const { Live2DModel } = await import('pixi-live2d-display/cubism4')
  Live2DModel.registerTicker(PIXI.Ticker)
  status('model.loading')
  document.body.style.background = state.transparent ? 'transparent' : '#2a1d28'
  app = new PIXI.Application({
    view: canvas,
    resizeTo: window,
    autoStart: true,
    antialias: true,
    backgroundAlpha: 0,
    resolution: Math.min(window.devicePixelRatio || 1, 2),
    autoDensity: true,
  })
  app.ticker.maxFPS = Math.max(15, Math.min(120, Number(state.renderFps) || 60))
  model = await Live2DModel.from(state.modelUrl, { autoInteract: true })
  modelNaturalWidth = Math.max(1, model.width)
  modelNaturalHeight = Math.max(1, model.height)
  app.stage.addChild(model)
  model.internalModel?.on('beforeModelUpdate', updatePerformanceParameters)
  model.on('hit', (areas) => {
    if (bridge && Array.isArray(areas) && areas.length)
      bridge.hitArea(String(areas[0]))
  })
  if (state.eyeTracking) {
    canvas.addEventListener('pointermove', (event) => {
      const rect = canvas.getBoundingClientRect()
      const x = ((event.clientX - rect.left) / Math.max(1, rect.width)) * 2 - 1
      const y = -(((event.clientY - rect.top) / Math.max(1, rect.height)) * 2 - 1)
      model?.focus(x, y)
    })
  }
  fitModel()
  speaking = Boolean(state.speaking)
  mouthTarget = Math.max(0, Math.min(1, Number(state.mouthOpen) || 0))
  currentEmotion = String(state.emotion || 'neutral')
  await applyEmotion(currentEmotion)
  status('model.loaded')
}

window.addEventListener('resize', fitModel)
window.addEventListener('error', (event) => fail(event.error || event.message))
window.addEventListener('unhandledrejection', (event) => fail(event.reason))

if (!window.qt?.webChannelTransport || !window.QWebChannel) {
  console.error('QWebChannel transport is unavailable')
}
else {
  new window.QWebChannel(window.qt.webChannelTransport, (channel) => {
    bridge = channel.objects.maicaAvatar
    bridge.command.connect(handleCommand)
    bridge.initialState((raw) => {
      try {
        start(JSON.parse(raw)).catch(fail)
      }
      catch (error) {
        fail(error)
      }
    })
    status('bridge.ready')
  })
}
