import { Component, input, output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Approval } from '../api';

/** Explicit human approval: reviewer name + typing APPROVE. Never pre-filled. */
@Component({
  selector: 'app-approval-form',
  imports: [FormsModule],
  template: `
    <div class="card">
      <b>{{ label() }}</b>
      <p class="small muted">{{ help() }}</p>
      <div class="row">
        <input [(ngModel)]="reviewer" placeholder="Your name" />
        <input [(ngModel)]="confirm" placeholder="Type APPROVE" />
        <input [(ngModel)]="notes" placeholder="Notes (optional)" style="flex:1; min-width: 10rem" />
        @if (allowAcknowledge()) {
          <label class="small"><input type="checkbox" [(ngModel)]="ack" />
            I fact-checked every flagged claim myself</label>
        }
        <button class="primary" [disabled]="disabled() || confirm !== 'APPROVE' || !reviewer.trim()"
                (click)="submit()">Approve</button>
      </div>
    </div>
  `,
})
export class ApprovalForm {
  label = input.required<string>();
  help = input('');
  disabled = input(false);
  allowAcknowledge = input(false);
  approve = output<Approval>();
  reviewer = '';
  confirm = '';
  notes = '';
  ack = false;

  submit() {
    this.approve.emit({ confirm: this.confirm, reviewer: this.reviewer.trim(), notes: this.notes,
                        acknowledge_flags: this.ack });
    this.confirm = '';
  }
}
