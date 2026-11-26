const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  getInitial: () => ipcRenderer.invoke('get-initial'),
  saveSettings: (s) => ipcRenderer.invoke('save-settings', s),
  onRates: (cb) => ipcRenderer.on('rates', (_e, state) => cb(state)),
  onFocusInput: (cb) => ipcRenderer.on('focus-input', () => cb()),
  hide: () => ipcRenderer.send('hide'),
  minimize: () => ipcRenderer.send('minimize'),
  refresh: () => ipcRenderer.send('refresh'),
  copy: (text) => ipcRenderer.send('copy', text),
});
