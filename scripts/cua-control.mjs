/** Persistent observe/act adapter for an existing cua App/Tab. No dependencies. */
export function createController(target, {kind = 'app', maxFrameAgeMs = 60000} = {}) {
  if (!['app', 'tab'].includes(kind)) throw new Error('kind must be app or tab');
  let serial = 0;
  let frame = null;

  async function observe({visual = false} = {}) {
    // Native observations emit their text/image directly in cua_repl.
    const state = visual
      ? (await target.getAXStateAndScreenshot({disableDiffing: true})).state
      : await target.getAXState({disableDiffing: true});
    frame = {id: ++serial, visual, time: Date.now()};
    return {frame_id: frame.id, visual, state};
  }

  function prepare(actions, stableLayout) {
    if (!Array.isArray(actions) || actions.length < 1 || actions.length > 16)
      throw new Error('Provide 1..16 actions');
    for (const [i, action] of actions.entries()) {
      if (!action || typeof action !== 'object') throw new Error('Invalid action');
      if (action.type === 'click') {
        if (Array.isArray(action.target)) {
          if (!frame.visual) throw new Error('Coordinate clicks require a visual observation');
          if (action.target.length !== 2 || !action.target.every(x => Number.isFinite(x) && x >= 0))
            throw new Error('Expected [x,y]');
          if (i > 0 && !stableLayout) throw new Error('Later coordinate clicks require stableLayout=true');
        } else if (!Number.isInteger(action.target) || action.target < 0) {
          throw new Error('Click target must be an observed element index or [x,y]');
        }
      } else if (action.type === 'keypress') {
        if (typeof action.key !== 'string' || !action.key) throw new Error('Expected key');
      } else if (action.type === 'paste') {
        if (typeof action.text !== 'string' || action.text.length > 4000) throw new Error('Invalid text');
      } else if (action.type === 'scroll') {
        if (!['up', 'down', 'left', 'right'].includes(action.direction)) throw new Error('Invalid direction');
      } else {
        throw new Error(`Unsupported action: ${action.type}`);
      }
      if ((action.type === 'scroll' || action.changesLayout) && i < actions.length - 1)
        throw new Error('End the batch at a layout change and observe before continuing');
    }
  }

  async function act(actions, {frame_id, stableLayout = false, visualAfter = false} = {}) {
    const started = performance.now();
    let completed = 0;
    try {
      if (!frame || frame_id !== frame.id || Date.now() - frame.time > maxFrameAgeMs)
        throw new Error('Stale frame: observe again');
      prepare(actions, stableLayout);
      for (const action of actions) {
        if (action.type === 'click') await target.click(action.target);
        else if (action.type === 'keypress') {
          if (kind === 'tab') await target.pressKey(action.element ?? null, action.key);
          else await target.pressKey(action.key);
        } else if (action.type === 'paste') {
          if (kind === 'tab') await target.paste(action.element ?? null, action.text);
          else await target.paste(action.text);
        } else if (action.type === 'scroll') {
          await target.scroll(action.target, action.direction, action.pages ?? 1);
        }
        completed++;
      }
      frame = null;
      const observation = await observe({visual: visualAfter});
      return {status: 'OK', completed, observation, elapsed_ms: Math.round(performance.now() - started)};
    } catch (error) {
      frame = null;
      return {status: completed ? 'PARTIAL' : 'REFUSED', completed,
              needs_observation: true, reason: String(error.message ?? error)};
    }
  }

  return {observe, act};
}
