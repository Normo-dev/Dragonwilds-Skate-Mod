//! One host-requested mount gesture through the original input and graphs.
//! No clip timing, board trajectory, physical transition or animation is set here.
use super::{Pose,Session};
use skate_core::{input::gameplay_map::GameplayActions,player::state::PhysicalStateId};

#[derive(Clone,Copy,Default)]
pub(super) enum State {
    #[default] Idle,
    Waiting { since:u64 },
    Press { since:u64 },
    Mounting { since:u64 },
    Complete,
    CompleteReleased,
    Failed(&'static str),
}
pub struct SummonStatus {
    pub phase:&'static str,
    pub error:Option<&'static str>,
    pub motion_state:String,
    pub holding_board:bool,
    pub supported:bool,
    pub locomotion:u32,
}
impl Session {
    /// Prepare an off-board source actor, then expose every subsequent original
    /// mount animation frame to the host. Ordinary activate remains immediate.
    pub fn summon(&mut self,spawn:[f32;3],heading:f32)->Result<Pose,String> {
        let pose=self.position_for_entry(spawn,heading,false)?;
        self.summon=State::Waiting{since:self.physics.ticks};
        Ok(pose)
    }
    pub fn summon_status(&self)->SummonStatus {
        let (phase,error)=match self.summon {
            State::Idle=>("idle",None),State::Waiting{..}=>("waiting_ground",None),
            State::Press{..}=>("mount_queued",None),State::Mounting{..}=>("mounting",None),
            State::Complete|State::CompleteReleased=>("riding",None),State::Failed(error)=>("failed",Some(error)),
        };
        let mut names=Vec::new();let mut state=self.skater.animation.motion_controller.frame.current;
        while let Some(id)=state {
            let value=&self.graphs.motion.binding.states[id];names.push(value.name.as_str());state=value.parent;
        }
        names.reverse();
        let p=&self.skater.player_input.physical.off_board;
        SummonStatus{phase,error,motion_state:names.join("."),holding_board:p.flag_311!=0,
            supported:self.skater.biped_ground.contact.flags_176&1!=0,locomotion:p.locomotion_state_84}
    }
    pub(super) fn summon_actions(&mut self,input:GameplayActions)->(GameplayActions,bool) {
        if matches!(self.summon,State::Idle|State::Complete|State::CompleteReleased|State::Failed(_)){
            return(self.release_mount_button(input),false);
        }
        let status=self.summon_status();
        let p=&self.skater.player_input.physical.off_board;
        let ready=self.skater.player_state.current()==PhysicalStateId::BipedGround
            && status.locomotion==0 && status.supported && status.holding_board
            && status.motion_state.starts_with("Motion.OffBoard.OBGround.Locomotion.Stand.")
            && p.flag_304==0 && p.returning_board_313==0 && p.dropping_board_322==0 && p.retrieving_board_323==0;
        let mut press=false;
        let active=match self.summon {
            State::Waiting{since}=>{
                if self.physics.ticks.saturating_sub(since)>600 {self.summon=State::Failed("Original actor did not reach a supported standing pose");false}
                else {if ready {self.summon=State::Press{since:self.physics.ticks};}true}
            },
            State::Press{since}=>{
                // The preceding waiting tick published a released Y value.
                if ready {press=true;self.summon=State::Mounting{since:self.physics.ticks};}
                else {self.summon=State::Waiting{since};}
                true
            },
            State::Mounting{since}=>{
                let mounted=status.motion_state.split('.').any(|name|name=="OnBoard")
                    && matches!(self.skater.player_input.physical.state.category_12,100|200|400);
                if mounted {self.summon=State::Complete;false}
                else if self.physics.ticks.saturating_sub(since)>600 {self.summon=State::Failed("Original mount graph did not reach an on-board state");false}
                else {true}
            },
            _=>false,
        };
        if !active {return(self.release_mount_button(input),false);}
        // During the one-time entry, movement/trick input waits for the stock
        // transition. Preserve look input. Slot15 is the stock Y action79;
        // DerivedControllerInput still owns its edge/timer and both graphs.
        let mut values=[0.;18];values[3]=input.values()[3];values[4]=input.values()[4];values[15]=if press {1.} else {0.};
        (GameplayActions::from_values(values),true)
    }
    fn release_mount_button(&mut self,input:GameplayActions)->GameplayActions {
        if !matches!(self.summon,State::Complete){return input;}
        // A human holding Y while selecting the mount must release it before
        // it can become another source toggle. All other riding input resumes.
        let mut values=*input.values();
        if values[15]==0. {self.summon=State::CompleteReleased;}
        else {values[15]=0.;}
        GameplayActions::from_values(values)
    }
}
