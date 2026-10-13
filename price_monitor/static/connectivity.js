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
      checking: ['Vérification de la connexion…', 'warning'],
      offline: ['Connexion Internet indisponible', 'error'],
      'service-unavailable': ['Application inaccessible', 'error'],
      'sources-unavailable': ['Internet connecté · sources indisponibles', 'warning'],
      partial: ['Internet connecté · collecte partielle', 'warning'],
      online: ['Internet connecté', ''],
      demo: ['Internet connecté · démonstration', 'warning']
    };
    this.update(...labels[state]);
    // Checking is transient: retain the last known state for recovery notices.
    if (state === 'checking' || state === this.previous) return;
    const previous = this.previous;
    this.previous = state;
    if (state === 'offline') {
      this.notify('Connexion Internet perdue. Les derniers prix enregistrés restent disponibles.', true);
    } else if (state === 'service-unavailable') {
      this.notify('Connexion à l’application perdue. Vérifiez que le serveur est démarré.', true);
    } else if (previous === 'offline' || previous === 'service-unavailable') {
      const message = previous === 'offline' ? 'Connexion Internet rétablie.' : 'Connexion à l’application rétablie.';
      this.notify(`${message}${state === 'sources-unavailable' ? ' En attente d’une collecte réussie.' : ''}`);
      this.onRecovery();
    } else if (state === 'sources-unavailable') {
      this.notify('Internet est accessible, mais aucune source de prix ne répond. Nouvelle tentative automatique à la prochaine collecte.', true);
    } else if (state === 'partial') {
      this.notify(previous === 'sources-unavailable' ? 'La collecte fonctionne à nouveau. Certaines sources restent indisponibles.' : 'Certaines sources de prix sont indisponibles. La collecte continue avec les autres sources.');
    } else if (previous === 'sources-unavailable' || previous === 'partial') {
      this.notify('Les sources de prix sont de nouveau disponibles.');
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
