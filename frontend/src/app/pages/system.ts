import { Component, inject, signal } from '@angular/core';
import { Api, errorText } from '../api';

@Component({
  selector: 'app-system',
  template: `
    <h1>System &amp; hardware</h1>
    @if (error()) { <div class="error">{{ error() }}</div> }
    @if (sys(); as s) {
      <div class="card">
        <div><b>GPU:</b>
          @if (s['gpu']; as g) { {{ g.name }} — {{ g.used_mib }} / {{ g.total_mib }} MiB used }
          @else { none visible (CPU mode) }
        </div>
        <div><b>RAM available:</b> {{ s['ram']?.MemAvailable }} / {{ s['ram']?.MemTotal }} MiB</div>
        <div><b>GPU lock:</b> {{ s['gpu_lock'] || 'free' }}</div>
        <div><b>LLM:</b> {{ s['llm']?.model }} at {{ s['llm']?.host }} · <b>encoder setting:</b> {{ s['encoder_setting'] }}</div>
        <div><b>Queue:</b> {{ s['queue']?.length || 0 }} running/queued task(s)</div>
      </div>
    }
    <div class="row"><h2>Doctor</h2>
      <button (click)="runDoctor(true)" [disabled]="busy()">Run (incl. Ollama)</button></div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Check</th><th>Status</th><th>Detail</th><th>Fix</th></tr></thead>
        <tbody>
          @for (c of checks(); track c.name) {
            <tr><td>{{ c.name }}</td>
              <td [class.ok]="c.status==='ok'" [class.warn]="c.status==='warn'" [class.bad]="c.status==='fail'">{{ c.status }}</td>
              <td class="small">{{ c.detail }}</td><td class="small muted">{{ c.hint }}</td></tr>
          }
        </tbody>
      </table>
    </div>
  `,
})
export class SystemPage {
  private api = inject(Api);
  sys = signal<Record<string, any> | null>(null);
  checks = signal<any[]>([]);
  busy = signal(false);
  error = signal('');

  constructor() {
    this.api.system().then((s) => this.sys.set(s)).catch((e) => this.error.set(errorText(e)));
    this.runDoctor(false);
  }

  async runDoctor(network: boolean) {
    this.busy.set(true);
    try { this.checks.set(await this.api.doctor(network)); }
    catch (e) { this.error.set(errorText(e)); }
    finally { this.busy.set(false); }
  }
}
