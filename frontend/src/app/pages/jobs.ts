import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { Api, JobRow, errorText } from '../api';

@Component({
  selector: 'app-jobs',
  imports: [RouterLink, FormsModule],
  template: `
    <h1>Jobs</h1>
    <div class="card row">
      <input [(ngModel)]="topic" placeholder="Free topic (or create from the calendar)"
             style="flex:1; min-width: 14rem" />
      <button class="primary" [disabled]="topic.trim().length < 3" (click)="create()">New job</button>
    </div>
    @if (error()) { <div class="error">{{ error() }}</div> }
    <div class="table-wrap">
      <table>
        <thead><tr><th>Job</th><th>Day</th><th>State</th><th>Topic</th><th>Updated</th></tr></thead>
        <tbody>
          @for (j of jobs(); track j.id) {
            <tr>
              <td><a [routerLink]="['/jobs', j.id]"><code>{{ j.id }}</code></a></td>
              <td>{{ j.day ?? '' }}</td>
              <td><span class="pill {{ j.state }}">{{ j.state }}</span>
                @if (j.failed_stage) { <span class="small bad"> {{ j.failed_stage }}</span> }</td>
              <td>{{ j.topic }}</td>
              <td class="small muted">{{ j.updated }}</td>
            </tr>
          } @empty {
            <tr><td colspan="5" class="muted">No jobs yet.</td></tr>
          }
        </tbody>
      </table>
    </div>
  `,
})
export class JobsPage {
  private api = inject(Api);
  private router = inject(Router);
  jobs = signal<JobRow[]>([]);
  error = signal('');
  topic = '';

  constructor() { this.load(); }

  async load() {
    try { this.jobs.set(await this.api.jobs()); } catch (e) { this.error.set(errorText(e)); }
  }

  async create() {
    try {
      const j = await this.api.createJob({ topic: this.topic.trim() });
      await this.router.navigate(['/jobs', j.id]);
    } catch (e) { this.error.set(errorText(e)); }
  }
}
