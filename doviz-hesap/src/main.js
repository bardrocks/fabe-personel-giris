const { app, BrowserWindow, Tray, Menu, globalShortcut, ipcMain, nativeImage, screen, clipboard } = require('electron');
const path = require('path');
const fs = require('fs');
const { Scraper } = require('./scraper');

const SHORTCUT = 'CommandOrControl+Shift+D';

const DEFAULT_SETTINGS = {
  tlSide: 'alis',        // USD -> TL hangi kurdan: 'alis' | 'satis'
  tlAdjust: 0,           // USD/TL kuruna eklenecek düzeltme (ör. -0.05)
  pariteSide: 'alis',    // EUR/USD paritesi hangi taraftan
  pariteAdjust: -0.01,   // 1,0941 yerine 1,0841 -> -0,0100
  alwaysOnTop: true,
  openAtLogin: false,
};

let win = null;
let tray = null;
let scraper = null;
let lastState = null;
let quitting = false;

// ---- Ayarlar ----
const settingsFile = () => path.join(app.getPath('userData'), 'ayarlar.json');
function loadSettings() {
  try {
    return { ...DEFAULT_SETTINGS, ...JSON.parse(fs.readFileSync(settingsFile(), 'utf8')) };
  } catch (_) {
    return { ...DEFAULT_SETTINGS };
  }
}
function saveSettings(s) {
  try { fs.writeFileSync(settingsFile(), JSON.stringify(s, null, 2)); } catch (_) {}
}
let settings = DEFAULT_SETTINGS;

// ---- Pencere ----
function createWindow() {
  const { workArea } = screen.getPrimaryDisplay();
  const width = 400;
  const height = 440;
  win = new BrowserWindow({
    width,
    height,
    x: workArea.x + workArea.width - width - 16,
    y: workArea.y + workArea.height - height - 16,
    minWidth: 340,
    minHeight: 420,
    frame: false,
    resizable: true,
    skipTaskbar: false,
    alwaysOnTop: settings.alwaysOnTop,
    show: false,
    title: 'Fabe Döviz Hesap',
    icon: iconPath(),
    backgroundColor: '#0f172a',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  win.loadFile(path.join(__dirname, 'index.html'));
  win.once('ready-to-show', () => showWindow());
  win.on('close', (e) => {
    if (!quitting) { e.preventDefault(); win.hide(); }
  });
}

function showWindow() {
  if (!win) return;
  win.show();
  win.focus();
  win.webContents.send('focus-input');
}

function toggleWindow() {
  if (!win) return;
  if (win.isVisible() && win.isFocused()) win.hide();
  else showWindow();
}

function iconPath() {
  const p = path.join(__dirname, '..', 'build', 'icon.png');
  return fs.existsSync(p) ? p : undefined;
}

function createTray() {
  const p = iconPath();
  const img = p ? nativeImage.createFromPath(p).resize({ width: 16, height: 16 }) : nativeImage.createEmpty();
  tray = new Tray(img);
  tray.setToolTip('Fabe Döviz Hesap (Ctrl+Shift+D)');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Göster / Gizle  (Ctrl+Shift+D)', click: toggleWindow },
    { label: 'Kurları yenile', click: () => scraper && scraper.refreshNow() },
    { type: 'separator' },
    { label: 'Çıkış', click: () => { quitting = true; app.quit(); } },
  ]));
  tray.on('click', toggleWindow);
}

// ---- IPC ----
ipcMain.handle('get-initial', () => ({ settings, state: lastState, shortcut: 'Ctrl+Shift+D' }));
ipcMain.handle('save-settings', (_e, s) => {
  settings = { ...settings, ...s };
  saveSettings(settings);
  if (win) win.setAlwaysOnTop(!!settings.alwaysOnTop);
  try { app.setLoginItemSettings({ openAtLogin: !!settings.openAtLogin }); } catch (_) {}
  return settings;
});
ipcMain.on('hide', () => win && win.hide());
ipcMain.on('minimize', () => win && win.minimize());
ipcMain.on('refresh', () => scraper && scraper.refreshNow());
ipcMain.on('copy', (_e, text) => clipboard.writeText(String(text)));

// ---- Uygulama ----
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', showWindow);
  app.whenReady().then(() => {
    app.setAppUserModelId('com.fabe.dovizhesap');
    settings = loadSettings();
    createWindow();
    createTray();
    globalShortcut.register(SHORTCUT, toggleWindow);
    scraper = new Scraper((state) => {
      lastState = state;
      if (win && !win.isDestroyed()) win.webContents.send('rates', state);
    });
    scraper.start();
  });
  app.on('before-quit', () => { quitting = true; });
  app.on('will-quit', () => {
    globalShortcut.unregisterAll();
    if (scraper) scraper.stop();
  });
  app.on('window-all-closed', (e) => e.preventDefault());
}
