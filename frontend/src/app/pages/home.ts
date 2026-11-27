import { Component, OnDestroy, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { Api, Overview, errorText } from '../api';

@Component({
  selector: 'app-home',
  imports: [RouterLink],
  template: `
    <h1>Good to see you</h1>
    <p class="muted">Pick a topic, check the script, the pictures and the video. The rest runs by itself.</p>
    @if (error()) { <div class="banner error">{{ error() }}</div> }

    @if (o(); as o) {
      @if (!o.reviewer_set) {
        <div class="banner info">Tell us your name once in <a routerLink="/settings">Settings</a>:
          it is recorded with your approvals.</div>
      }
      @if (!o.ai_images) {
        <div class="banner warn">The AI image generator is off. Real photos still work; scenes without a
          suitable photo will ask you to upload one. To start it, run on the laptop:
          <code>docker compose -f .devcontainer/compose.yaml -f .devcontainer/compose.local.yaml --profile comfyui up -d comfyui</code></div>
      }

      @if (o.upcoming[0]; as t) {
        <div class="card hero">
          <div class="small muted">{{ t.date === o.today ? 'Today' : 'Next up' }} · day {{ t.day }} · {{ t.date }}</div>
          <h2 class="topic">{{ t.topic }}</h2>
          @if (t.job) {
            <a class="button primary big" [routerLink]="['/videos', t.job.id]">Open this video →</a>
          } @else {
            <button class="primary big" (click)="make(t.day)" [disabled]="busy()">▶ Make this video</button>
          }
        </div>
      }

      <div class="cols">
        <section>
          <h2>Waiting for you ({{ o.review.length }})</h2>
          @for (r of o.review; track r.id) {
            <a class="item card" [routerLink]="['/videos', r.id]">
              <div><b>{{ r.topic }}</b><div class="small muted">{{ r.date ?? 'free topic' }}</div></div>
              <span class="chip review">Check the {{ r.step.toLowerCase() }}</span>
            </a>
          } @empty { <p class="muted small">Nothing to check right now.</p> }
        </section>
        <section>
          <h2>In progress ({{ o.in_progress.length }})</h2>
          @for (r of o.in_progress; track r.id) {
            <a class="item card" [routerLink]="['/videos', r.id]">
              <div><b>{{ r.topic }}</b>
                <div class="small muted">{{ r.task?.progress?.message ?? 'Working…' }}</div></div>
              <span class="chip working">{{ r.step }}</span>
            </a>
          } @empty { <p class="muted small">Nothing running.</p> }
        </section>
      </div>

      @if (o.upcoming.length > 1) {
        <h2>Coming up</h2>
        <div class="card">
          @for (t of o.upcoming.slice(1); track t.day) {
            <div class="row spread line">
              <span><span class="muted small">{{ t.date }}</span> &nbsp; {{ t.topic }}</span>
              @if (t.job) { <a [routerLink]="['/videos', t.job.id]">open</a> }
              @else { <button class="link" (click)="make(t.day)" [disabled]="busy()">Make video</button> }
            </div>
          }
          <a routerLink="/calendar" class="small">Full calendar →</a>
        </div>
      }
    }
  `,
  styles: `
    .hero { padding: 1.5rem; border-left: 4px solid var(--accent); }
    .topic { font-size: 1.4rem; margin: .4rem 0 1rem; }
    .cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem; }
    .item { display: flex; justify-content: space-between; gap: 1rem; align-items: center;
            text-decoration: none; color: var(--fg); margin-bottom: .6rem; }
    .item:hover { border-color: var(--accent); }
    .line { padding: .45rem 0; border-bottom: 1px solid var(--line); }
  `,
})
export class HomePage implements OnDestroy {
  private api = inject(Api);
  private router = inject(Router);
  o = signal<Overview | null>(null);
  error = signal('');
  busy = signal(false);
  private timer = setInterval(() => this.load(), 4000);

  constructor() { this.load(); }
  ngOnDestroy() { clearInterval(this.timer); }

  async load() {
    try { this.o.set(await this.api.overview()); this.error.set(''); }
    catch (e) { this.error.set(errorText(e)); }
  }

  async make(day: number) {
    this.busy.set(true);
    try {
      const t = await this.api.make({ day });
      await this.router.navigate(['/videos', t.job_id]);
    } catch (e) { this.error.set(errorText(e)); }
    finally { this.busy.set(false); }
  }
}
