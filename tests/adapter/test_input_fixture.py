"""Neutral test transport. Never imports SDL or inspects any real input/window."""
class NoInput:
    def sample(self, host, active):
        return {'controls':{'buttons':0,'triggers':[0,0],'left':[0,0],'right':[0,0]},
                'focused':True,'allowed':bool(active),'camera_changed':False,
                'camera_mode':host.get('test_camera','high'),
                'controller_connected':False,'controller_name':'isolated test (no input)'}

    def close(self):
        pass


def create_input(_root):
    return NoInput()
