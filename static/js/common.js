// Helpers shared by the page components. Each mixin returns properties to
// spread into a page's Alpine data object: { ...toastMixin(), ...page }.
// Loaded before Alpine (which is deferred), so the page scripts can use them.

function formatMixin() {
    return {
        formatFileSize(bytes) {
            if (!bytes) return '0 B';
            if (bytes < 1024) return bytes + ' B';
            if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
            if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
            return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
        },
        // Epoch seconds (file mtimes) as a local date and time.
        formatFullDate(timestamp) {
            if (!timestamp) return '';
            return new Date(timestamp * 1000).toLocaleString();
        },
        // Player position, m:ss.
        formatVideoTime(secs) {
            if (secs == null || isNaN(secs)) return '0:00';
            const m = Math.floor(secs / 60);
            const s = Math.floor(secs % 60).toString().padStart(2, '0');
            return `${m}:${s}`;
        },
    };
}

// Pair with the toasts() macro in templates/_macros.html.
function toastMixin(defaultDurationMs = 4000) {
    return {
        toasts: [],
        _toastSeq: 0,
        toast(message, type = 'success', duration = defaultDurationMs) {
            const id = ++this._toastSeq;
            this.toasts.push({ id, message, type });
            setTimeout(() => { this.toasts = this.toasts.filter(t => t.id !== id); }, duration);
        },
    };
}

// Controls shared by the recordings and clips players. *videoRef* is the
// x-ref of the page's <video>; the fullscreen target is $refs.playerWrapper.
// A page reacting to speed changes (POV videos) defines onSpeedChange(speed).
const PLAYER_SEEK_STEP_SECONDS = 10;
const PLAYER_CONTROLS_HIDE_MS = 2500;

function videoPlayerMixin(videoRef) {
    return {
        _video() {
            return this.$refs[videoRef];
        },
        togglePlay() {
            const v = this._video();
            if (!v) return;
            v.paused ? v.play() : v.pause();
        },
        seekBy(seconds) {
            const v = this._video();
            if (v) v.currentTime = Math.max(0, Math.min(v.duration || 0, v.currentTime + seconds));
        },
        setSpeed(speed) {
            this.speed = speed;
            const v = this._video();
            if (v) v.playbackRate = speed;
            this.onSpeedChange?.(speed);
        },
        toggleFullscreen() {
            const w = this.$refs.playerWrapper;
            if (!w) return;
            document.fullscreenElement ? document.exitFullscreen() : w.requestFullscreen();
        },
        // In fullscreen the controls hide again after a moment of no movement.
        showControls() {
            this.controlsVisible = true;
            clearTimeout(this._controlsTimer);
            if (document.fullscreenElement) {
                this._controlsTimer = setTimeout(() => { this.controlsVisible = false; }, PLAYER_CONTROLS_HIDE_MS);
            }
        },
        // Whether a keydown is meant for the player at all.
        playerKeyAllowed(e) {
            if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return false;
            // Leave browser shortcuts (Ctrl+F, ...) and Space-on-a-button alone.
            if (e.ctrlKey || e.metaKey || e.altKey) return false;
            return !(e.key === ' ' && e.target.closest?.('button, a'));
        },
        // Keys both players share. True if *e* was handled.
        handlePlayerKey(e) {
            const actions = {
                ' ': () => this.togglePlay(), k: () => this.togglePlay(),
                ArrowRight: () => this.seekBy(PLAYER_SEEK_STEP_SECONDS),
                ArrowLeft: () => this.seekBy(-PLAYER_SEEK_STEP_SECONDS),
                l: () => this.seekBy(PLAYER_SEEK_STEP_SECONDS),
                j: () => this.seekBy(-PLAYER_SEEK_STEP_SECONDS),
                f: () => this.toggleFullscreen(),
                m: () => { this.isMuted = !this.isMuted; },
            };
            const key = e.key.length === 1 && e.key !== ' ' ? e.key.toLowerCase() : e.key;
            const action = actions[key];
            if (!action) return false;
            e.preventDefault();
            action();
            return true;
        },
    };
}
