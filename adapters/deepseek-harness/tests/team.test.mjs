import {test} from 'node:test';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import * as plugin from '../plugin/lib/index.js';
import {teamAcceptance} from './team-fixture.mjs';
import {negativeTeamChecks} from './team-negative.mjs';
test('Official DSH Team: real member provenance, bounded perspectives, disagreement, cross-checks and durable exact artifact binding',async()=>{
 const root=await mkdtemp(join(tmpdir(),'bugate-team-acceptance-'));
 await teamAcceptance(join(root,'project'),plugin,{evidencePath:process.env.BUGATE_TEAM_EVIDENCE,negativeChecks:negativeTeamChecks});
});
