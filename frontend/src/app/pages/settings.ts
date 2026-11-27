import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api, AppSettings, LANGUAGES, VOICES, errorText } from '../api';

@Component({
  selector: 'app-settings',
  imports: [FormsModule],
  template: `
    <h1>Settings</h1>
    @if (error()) { <div class="banner error">{{ error() }}</div> }
    @if (saved()) { <div class="banner ok">Saved.</div> }
    <div class="card">
      <label class="field"><b>Your name</b>
        <span class="small muted">Recorded with each approval, so you know who checked what.</span>
        <input [(ngModel)]="reviewer" placeholder="Your name" /></label>
      <label class="field"><b>Subtitle language for new videos</b>
        <span class="small muted">You can still change it for each video.</span>
        <select [(ngModel)]="subtitles">
          @for (l of langs; track l[0]) { <option [value]="l[0]">{{ l[1] }}</option> }
        </select></label>
      <label class="field"><b>Voice for new videos</b>
        <span class="small muted">You can still change it for each video. ▶ plays a short sample.</span>
        <span class="row" style="flex-wrap:nowrap">
          <select [(ngModel)]="voice">
            @for (v of voices; track v[0]) { <option [value]="v[0]">{{ v[1] }}</option> }
          </select>
          <button (click)="listen()" [disabled]="playing()">{{ playing() ? '…' : '▶' }}</button>
        </span></label>
      <button class="primary" (click)="save()">Save</button>
    </div>
    @if (s(); as s) {
      <div class="card small">
        <b>Video length:</b> {{ s.min_duration_s }}–{{ s.max_duration_s }} seconds (scripts are checked before
        you approve them; the voice is sped up by at most 8% if it runs slightly long).<br />
        <b>Picture sources, in order:</b> {{ sourceNames(s.picture_sources) }}.
      </div>
    }

    <h2>System check</h2>
    <p class="small muted">Run this if something does not work.</p>
    <button (click)="check()" [disabled]="busy()">{{ busy() ? 'Checking…' : 'Run system check' }}</button>
    @if (checks().length) {
      <div class="table-wrap card" style="margin-top: .8rem">
        <table class="small">
          <thead><tr><th>Check</th><th>Status</th><th>Detail</th><th>How to fix</th></tr></thead>
          <tbody>
            @for (c of checks(); track c.name) {
              <tr><td>{{ c.name }}</td>
                <td [class.ok]="c.status === 'ok'" [class.warn]="c.status === 'warn'" [class.bad]="c.status === 'fail'">{{ c.status }}</td>
                <td>{{ c.detail }}</td><td class="muted">{{ c.hint }}</td></tr>
            }
          </tbody>
        </table>
      </div>
    }
  `,
  styles: `.field { display: grid; gap: .3rem; margin-bottom: 1rem; max-width: 26rem; }`,
})
export class SettingsPage {
  private api = inject(Api);
  s = signal<AppSettings | null>(null);
  checks = signal<any[]>([]);
  error = signal('');
  saved = signal(false);
  busy = signal(false);
  reviewer = '';
  subtitles = 'en';
  langs = Object.entries(LANGUAGES);
  voices = Object.entries(VOICES);
  voice = 'af_heart';
  playing = signal(false);

  constructor() {
    this.api.settings().then((s) => {
      this.s.set(s); this.reviewer = s.reviewer; this.subtitles = s.default_subtitles;
      this.voice = s.default_voice;
    }).catch((e) => this.error.set(errorText(e)));
  }

  sourceNames(order: string[]) {
    const names: Record<string, string> = { library: 'your uploads', commons: 'real photos (Wikimedia Commons)',
      openverse: 'real photos (Openverse)',
      comfyui: 'AI images', pexels: 'Pexels stock', graphics: 'graphics' };
    return order.map((o) => names[o] ?? o).join(' → ');
  }

  listen() {
    this.playing.set(true);
    const a = new Audio(this.api.voiceSampleUrl(this.voice));
    a.onended = a.onerror = () => this.playing.set(false);
    a.play().catch(() => this.playing.set(false));
  }

  async save() {
    try {
      this.s.set(await this.api.saveSettings({ reviewer: this.reviewer, default_subtitles: this.subtitles,
                                               default_voice: this.voice }));
      this.saved.set(true); setTimeout(() => this.saved.set(false), 2500);
    } catch (e) { this.error.set(errorText(e)); }
  }

  async check() {
    this.busy.set(true);
    try { this.checks.set(await this.api.doctor(true)); }
    catch (e) { this.error.set(errorText(e)); }
    finally { this.busy.set(false); }
  }
}
