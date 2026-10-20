// Keep notifications tied to changes, even when failed collections repeat.
class ConnectionMonitor {
  constructor({ update, notify, onRecovery = () => {} }) {
    this.update = update;
    this.notify = notify;
    this.onRecovery = onRecovery;
    this.browserOnline = true;
    this.serviceAvailable = true;
    this.internet = 'checking';
    this.collection = null;
    this.demo = false;
    this.previous = null;
    this.browserVersion = 0;
    this.pending = false;
  }

  setBrowserOnline(online) {
    if (online !== this.browserOnline) {
      this.browserVersion += 1;
      this.internet = 'checking';
    }
    this.browserOnline = online;
    this.render();
  }

  setServiceAvailable(available) {
    if (!available) this.internet = 'checking';
    this.serviceAvailable = available;
    this.render();
  }

  acceptStatus(data) {
    this.serviceAvailable = true;
    this.internet = data.status;
    this.collection = data.collection?.status || null;
    this.demo = Boolean(data.demo_mode);
    this.render();
  }

  render() {
    let state;
    if (!this.browserOnline) state = 'offline';
    else if (!this.serviceAvailable) state = 'service-unavailable';
    else if (this.internet === 'offline') state = 'offline';
    else if (this.internet !== 'online') state = 'checking';
    else if (this.demo) state = 'demo';
    else if (this.collection === 'error') state = 'sources-unavailable';
    else if (this.collection === 'partial') state = 'partial';
    else state = 'online';

    const labels = {
      checking: ['Checking connection…', 'warning'],
      offline: ['Internet connection unavailable', 'error'],
      'service-unavailable': ['Application unavailable', 'error'],
      'sources-unavailable': ['Internet connected · providers unavailable', 'warning'],
      partial: ['Internet connected · partial collection', 'warning'],
      online: ['Internet connected', ''],
      demo: ['Internet connected · demo', 'warning']
    };
    this.update(...labels[state]);
    // Checking is transient: retain the last known state for recovery notices.
    if (state === 'checking' || state === this.previous) return;
    const previous = this.previous;
    this.previous = state;
    if (state === 'offline') {
      this.notify('Internet connection lost. The latest saved prices remain available.', true);
    } else if (state === 'service-unavailable') {
      this.notify('Connection to the application lost. Check that the server is running.', true);
    } else if (previous === 'offline' || previous === 'service-unavailable') {
      const message = previous === 'offline' ? 'Internet connection restored.' : 'Connection to the application restored.';
      this.notify(`${message}${state === 'sources-unavailable' ? ' Waiting for a successful collection.' : ''}`);
      this.onRecovery();
    } else if (state === 'sources-unavailable') {
      this.notify('Internet is available, but no price provider is responding. Another attempt will run at the next collection.', true);
    } else if (state === 'partial') {
      this.notify(previous === 'sources-unavailable' ? 'Collection is working again. Some providers remain unavailable.' : 'Some price providers are unavailable. Collection continues with the others.');
    } else if (previous === 'sources-unavailable' || previous === 'partial') {
      this.notify('Price providers are available again.');
      this.onRecovery();
    }
  }

  async check() {
    if (this.pending) return;
    this.pending = true;
    const version = this.browserVersion;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch('/api/connectivity', { cache: 'no-store', signal: controller.signal });
      // Throttling does not mean the application or Internet is disconnected.
      if (response.status === 429) return;
      if (!response.ok) throw new Error('Connection status unavailable');
      const data = await response.json();
      if (version === this.browserVersion) this.acceptStatus(data);
    } catch {
      if (version === this.browserVersion) this.setServiceAvailable(false);
    } finally {
      clearTimeout(timeout);
      this.pending = false;
    }
  }
}

if (typeof module !== 'undefined' && module.exports) module.exports = ConnectionMonitor;
