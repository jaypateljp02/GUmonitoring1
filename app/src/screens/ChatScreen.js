import React, { useState, useEffect, useRef } from 'react';
import { 
  View, 
  Text, 
  StyleSheet, 
  TouchableOpacity, 
  FlatList, 
  TextInput, 
  ActivityIndicator, 
  Alert, 
  Platform, 
  KeyboardAvoidingView, 
  ScrollView,
  SafeAreaView
} from 'react-native';
import { api, authApi, tasksApi, getAuthToken } from '../services/api';
import { playAudio, isMuted } from '../services/audioService';
import { startRecording, stopRecording } from '../services/audioRecorderService';
import { theme } from '../theme';

export default function ChatScreen({ navigation }) {
  // Navigation states
  const [currentChat, setCurrentChat] = useState(null); // null means showing list of chats
  const [chats, setChats] = useState([]);
  const [usersMap, setUsersMap] = useState({});
  const [usersList, setUsersList] = useState([]);
  
  // Messaging states
  const [messages, setMessages] = useState([]);
  const [textInput, setTextInput] = useState('');
  const [loading, setLoading] = useState(true);
  const [chatLoading, setChatLoading] = useState(false);
  const [currentUser, setCurrentUser] = useState(null);
  
  // Voice Recording states
  const [isRecording, setIsRecording] = useState(false);
  const [voiceUploading, setVoiceUploading] = useState(false);

  // New Chat Dialog states
  const [showNewChatModal, setShowNewChatModal] = useState(false);
  const [isGroupSelection, setIsGroupSelection] = useState(false);
  const [groupNameInput, setGroupNameInput] = useState('');
  const [selectedUserIds, setSelectedUserIds] = useState([]);

  const wsRef = useRef(null);
  const flatListRef = useRef(null);

  // Load initial data: current user, users list, active chats list
  useEffect(() => {
    const initData = async () => {
      try {
        setLoading(true);
        // Get profile
        const profileRes = await authApi.get('/users/profile');
        setCurrentUser(profileRes.data);

        // Get users list for starting DMs/Groups
        const usersRes = await authApi.get('/users?active=true');
        const list = usersRes.data.filter(u => u.id !== profileRes.data.id);
        setUsersList(list);

        // Map users for fast lookup
        const map = {};
        usersRes.data.forEach(u => {
          map[u.id] = u;
        });
        setUsersMap(map);

        // Fetch chats
        await fetchChats();
      } catch (err) {
        console.error('Chat initial load error:', err);
        Alert.alert('Error', 'Failed to connect to chat services');
      } finally {
        setLoading(false);
      }
    };

    initData();
  }, []);

  const fetchChats = async () => {
    try {
      const res = await tasksApi.get('/chats');
      setChats(res.data || []);
    } catch (e) {
      console.log('Failed to fetch chats:', e);
    }
  };

  // Setup WebSocket and load message history when a chat is opened
  useEffect(() => {
    if (!currentChat) {
      // Disconnect socket if going back to list
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      return;
    }

    const openChat = async () => {
      try {
        setChatLoading(true);
        setMessages([]);

        // Get message history
        const res = await tasksApi.get(`/chats/${currentChat.id}/messages`);
        setMessages(res.data || []);

        // Resolve WS URL
        const baseURL = tasksApi.defaults.baseURL;
        let wsBase = baseURL.replace('http://', 'ws://').replace('https://', 'wss://');
        const wsUrl = `${wsBase}/chats/${currentChat.id}/ws`;

        console.log('[WebSocket] Connecting to:', wsUrl);
        const ws = new WebSocket(wsUrl);
        wsRef.current = ws;

        ws.onopen = () => {
          console.log('[WebSocket] Connection open');
        };

        ws.onmessage = async (e) => {
          const newMsg = JSON.parse(e.data);
          setMessages(prev => {
            // Check if already in list to avoid duplicates
            if (prev.find(m => m.id === newMsg.id)) return prev;
            return [...prev, newMsg];
          });

          // Text to Speech announcement for incoming messages
          if (newMsg.sender_id !== currentUser?.id) {
            const muted = await isMuted();
            if (!muted) {
              const sender = usersMap[newMsg.sender_id]?.name || 'Someone';
              const textContent = newMsg.message_text || 'sent a voice message';
              const messageAnnounce = `New message from ${sender}: ${textContent}`;
              
              const preferredLocale = currentUser?.preferred_locale || 'en';
              const ttsUrl = `${tasksApi.defaults.baseURL}/ai/tts?text=${encodeURIComponent(messageAnnounce)}&lang=${preferredLocale}`;
              await playAudio(ttsUrl);
            }
          }
        };

        ws.onerror = (err) => {
          console.error('[WebSocket] Error:', err);
        };

        ws.onclose = () => {
          console.log('[WebSocket] Closed');
        };

      } catch (err) {
        console.error('Failed to open chat:', err);
        Alert.alert('Error', 'Failed to open message history');
      } finally {
        setChatLoading(false);
      }
    };

    openChat();

    return () => {
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [currentChat]);

  // Send message
  const handleSendMessage = async () => {
    if (!textInput.trim()) return;
    const body = {
      message_text: textInput.trim(),
      voice_url: null,
      voice_transcript: null
    };
    setTextInput('');

    try {
      // Send message via HTTP API
      await tasksApi.post(`/chats/${currentChat.id}/messages`, body);
    } catch (err) {
      console.error('Failed to send text message:', err);
      Alert.alert('Error', 'Failed to send message');
    }
  };

  // Voice recording handlers
  const handleStartRecording = async () => {
    try {
      setIsRecording(true);
      await startRecording();
    } catch (err) {
      setIsRecording(false);
      Alert.alert('Mic Error', err.message || 'Microphone access failed');
    }
  };

  const handleStopRecording = async () => {
    try {
      setIsRecording(false);
      setVoiceUploading(true);
      
      const recordResult = await stopRecording();
      
      // Upload recording to media service
      const formData = new FormData();
      if (Platform.OS === 'web') {
        formData.append('file', recordResult.blob, 'voice.webm');
      } else {
        formData.append('file', {
          uri: recordResult.uri,
          name: 'voice.m4a',
          type: 'audio/m4a'
        });
      }

      console.log('[Chat] Uploading voice note...');
      const uploadRes = await tasksApi.post('/media/upload', formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        }
      });
      const voiceUrl = uploadRes.data.url;

      // Post voice message to chat channel
      const body = {
        message_text: '',
        voice_url: voiceUrl,
        voice_transcript: null
      };

      await tasksApi.post(`/chats/${currentChat.id}/messages`, body);
    } catch (err) {
      console.error('Voice messaging error:', err);
      Alert.alert('Voice Note Failed', 'Failed to upload or record voice message.');
    } finally {
      setVoiceUploading(false);
    }
  };

  // Direct chat or group chat creation
  const handleCreateDM = async (targetUserId) => {
    setShowNewChatModal(false);
    try {
      setLoading(true);
      const res = await tasksApi.post('', {
        is_group: false,
        participant_ids: [currentUser.id, targetUserId]
      });
      await fetchChats();
      setCurrentChat(res.data);
    } catch (err) {
      Alert.alert('Error', 'Could not initiate chat');
    } finally {
      setLoading(false);
    }
  };

  const handleCreateGroup = async () => {
    if (!groupNameInput.trim()) {
      Alert.alert('Group Name Required', 'Please enter a name for the group');
      return;
    }
    if (selectedUserIds.length === 0) {
      Alert.alert('Participants Required', 'Please select at least one employee');
      return;
    }

    setShowNewChatModal(false);
    try {
      setLoading(true);
      const res = await tasksApi.post('', {
        name: groupNameInput.trim(),
        is_group: true,
        participant_ids: [currentUser.id, ...selectedUserIds]
      });
      setGroupNameInput('');
      setSelectedUserIds([]);
      await fetchChats();
      setCurrentChat(res.data);
    } catch (err) {
      Alert.alert('Error', 'Could not create group');
    } finally {
      setLoading(false);
    }
  };

  const getChatName = (item) => {
    if (item.is_group) return item.name || 'Group Chat';
    
    // For direct message, find the other participant ID
    const otherId = item.participant_ids?.find(uid => uid !== currentUser?.id);
    return usersMap[otherId]?.name || 'Chat Room';
  };

  const renderChatItem = ({ item }) => {
    const isGroup = item.is_group;
    return (
      <TouchableOpacity 
        style={styles.chatCard}
        onPress={() => setCurrentChat(item)}
        activeOpacity={0.8}
      >
        <View style={styles.chatAvatar}>
          <Text style={styles.chatAvatarText}>{isGroup ? '👥' : '👤'}</Text>
        </View>
        <View style={{ flex: 1, marginLeft: 12 }}>
          <Text style={styles.chatName}>{getChatName(item)}</Text>
          <Text style={styles.chatSubtext}>
            {isGroup ? 'Group channel' : 'Direct message'}
          </Text>
        </View>
        <Text style={styles.chatArrow}>➔</Text>
      </TouchableOpacity>
    );
  };

  const renderMessageItem = ({ item }) => {
    const isMe = item.sender_id === currentUser?.id;
    const senderName = usersMap[item.sender_id]?.name || 'Employee';
    const messageTime = new Date(item.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    return (
      <View style={[styles.messageRow, isMe ? styles.messageRowMe : styles.messageRowOther]}>
        {!isMe && (
          <Text style={styles.messageSender}>{senderName}</Text>
        )}
        <View style={[styles.messageBubble, isMe ? styles.bubbleMe : styles.bubbleOther]}>
          {item.message_text ? (
            <Text style={[styles.messageText, isMe ? styles.textMe : styles.textOther]}>{item.message_text}</Text>
          ) : null}
          
          {item.voice_url ? (
            <View style={styles.audioWrapper}>
              <TouchableOpacity 
                style={styles.audioPlayButton}
                onPress={() => playAudio(item.voice_url)}
                activeOpacity={0.7}
              >
                <Text style={{ fontSize: 16 }}>▶️ Listen Voice Note</Text>
              </TouchableOpacity>
              {item.voice_transcript ? (
                <View style={styles.transcriptBox}>
                  <Text style={styles.transcriptText}>🗣️ "{item.voice_transcript}"</Text>
                </View>
              ) : (
                <View style={styles.transcriptBox}>
                  <Text style={[styles.transcriptText, { fontStyle: 'italic', color: '#94A3B8' }]}>
                    Transcribing...
                  </Text>
                </View>
              )}
            </View>
          ) : null}
          <Text style={styles.messageTime}>{messageTime}</Text>
        </View>
      </View>
    );
  };

  // If loading main profile/users map
  if (loading && chats.length === 0) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color="#3B82F6" />
        <Text style={{ color: '#94A3B8', marginTop: 12 }}>Connecting chat services...</Text>
      </View>
    );
  }

  // --- RENDERING VIEWS ---

  // 1. Single Chat View
  if (currentChat) {
    return (
      <SafeAreaView style={styles.container}>
        <KeyboardAvoidingView 
          style={{ flex: 1 }}
          behavior={Platform.OS === 'ios' ? 'padding' : undefined}
          keyboardVerticalOffset={Platform.OS === 'ios' ? 90 : 0}
        >
          {/* Header */}
          <View style={styles.chatHeader}>
            <TouchableOpacity 
              style={styles.backBtn}
              onPress={() => {
                setCurrentChat(null);
                fetchChats();
              }}
              activeOpacity={0.8}
            >
              <Text style={{ color: '#3B82F6', fontSize: 16, fontWeight: '700' }}>◀ Back</Text>
            </TouchableOpacity>
            <View style={styles.chatHeaderDetails}>
              <Text style={styles.chatHeaderTitle} numberOfLines={1}>{getChatName(currentChat)}</Text>
              <Text style={{ color: '#94A3B8', fontSize: 11 }}>
                {currentChat.is_group ? 'Active Group' : 'Direct Chat'}
              </Text>
            </View>
            <View style={{ width: 60 }} />
          </View>

          {/* Messages */}
          {chatLoading ? (
            <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center' }}>
              <ActivityIndicator size="small" color="#3B82F6" />
            </View>
          ) : (
            <FlatList
              ref={flatListRef}
              data={messages}
              renderItem={renderMessageItem}
              keyExtractor={item => item.id}
              contentContainerStyle={{ padding: 16, paddingBottom: 24 }}
              onContentSizeChange={() => flatListRef.current?.scrollToEnd({ animated: true })}
            />
          )}

          {/* Voice status banner */}
          {(isRecording || voiceUploading) && (
            <View style={styles.recordStatusBanner}>
              <Text style={styles.recordStatusText}>
                {isRecording ? '🎙️ Recording voice note... Tap microphone again to send' : '⚡ Transcribing & uploading...'}
              </Text>
            </View>
          )}

          {/* Input Panel */}
          <View style={styles.inputPanel}>
            <TouchableOpacity 
              style={[
                styles.micBtn, 
                isRecording && styles.micBtnRecording,
                voiceUploading && styles.micBtnUploading
              ]}
              onPress={isRecording ? handleStopRecording : handleStartRecording}
              disabled={voiceUploading}
              activeOpacity={0.8}
            >
              {voiceUploading ? (
                <ActivityIndicator color="#FFFFFF" size="small" />
              ) : (
                <Text style={{ fontSize: 20 }}>{isRecording ? '⏹️' : '🎤'}</Text>
              )}
            </TouchableOpacity>
            <TextInput
              style={styles.chatTextInput}
              value={textInput}
              onChangeText={setTextInput}
              placeholder="Type message here..."
              placeholderTextColor="#64748B"
              onSubmitEditing={handleSendMessage}
            />
            <TouchableOpacity 
              style={styles.sendBtn}
              onPress={handleSendMessage}
              activeOpacity={0.8}
            >
              <Text style={{ color: '#FFFFFF', fontWeight: 'bold' }}>Send</Text>
            </TouchableOpacity>
          </View>
        </KeyboardAvoidingView>
      </SafeAreaView>
    );
  }

  // 2. Chat List / Contacts selection modal
  if (showNewChatModal) {
    return (
      <SafeAreaView style={[styles.container, { padding: 16 }]}>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <Text style={{ color: theme.colors.text, fontSize: 20, fontWeight: '800' }}>
            {isGroupSelection ? 'Create Group Chat' : 'Start New Message'}
          </Text>
          <TouchableOpacity 
            style={styles.closeBtn}
            onPress={() => {
              setShowNewChatModal(false);
              setIsGroupSelection(false);
              setGroupNameInput('');
              setSelectedUserIds([]);
            }}
          >
            <Text style={{ color: '#EF4444', fontWeight: 'bold' }}>Cancel</Text>
          </TouchableOpacity>
        </View>

        {/* Toggle DM vs Group */}
        <View style={styles.segmentedControl}>
          <TouchableOpacity 
            style={[styles.segmentBtn, !isGroupSelection && styles.segmentBtnActive]}
            onPress={() => setIsGroupSelection(false)}
          >
            <Text style={[styles.segmentText, !isGroupSelection && styles.segmentTextActive]}>Direct DM</Text>
          </TouchableOpacity>
          <TouchableOpacity 
            style={[styles.segmentBtn, isGroupSelection && styles.segmentBtnActive]}
            onPress={() => setIsGroupSelection(true)}
          >
            <Text style={[styles.segmentText, isGroupSelection && styles.segmentTextActive]}>Group Chat</Text>
          </TouchableOpacity>
        </View>

        {isGroupSelection && (
          <View style={{ marginBottom: 16 }}>
            <Text style={styles.inputLabel}>Group Name</Text>
            <TextInput
              style={styles.groupInput}
              value={groupNameInput}
              onChangeText={setGroupNameInput}
              placeholder="e.g. Pune Fermentation Team"
              placeholderTextColor="#64748B"
            />
            <Text style={styles.inputLabel}>Select Members</Text>
          </View>
        )}

        <FlatList
          data={usersList}
          keyExtractor={item => item.id}
          renderItem={({ item }) => {
            const isSelected = selectedUserIds.includes(item.id);
            return (
              <TouchableOpacity
                style={[
                  styles.contactCard,
                  isSelected && styles.contactCardSelected
                ]}
                onPress={() => {
                  if (isGroupSelection) {
                    if (isSelected) {
                      setSelectedUserIds(prev => prev.filter(id => id !== item.id));
                    } else {
                      setSelectedUserIds(prev => [...prev, item.id]);
                    }
                  } else {
                    handleCreateDM(item.id);
                  }
                }}
                activeOpacity={0.8}
              >
                <View style={styles.contactAvatar}>
                  <Text style={{ fontSize: 16 }}>👤</Text>
                </View>
                <View style={{ flex: 1, marginLeft: 12 }}>
                  <Text style={styles.contactName}>{item.name}</Text>
                  <Text style={styles.chatSecondaryText}>{item.role}</Text>
                </View>
                {isGroupSelection && (
                  <View style={[styles.checkbox, isSelected && styles.checkboxSelected]}>
                    {isSelected && <Text style={{ color: '#FFFFFF', fontSize: 10 }}>✓</Text>}
                  </View>
                )}
              </TouchableOpacity>
            );
          }}
        />

        {isGroupSelection && (
          <TouchableOpacity 
            style={styles.groupSubmitBtn}
            onPress={handleCreateGroup}
          >
            <Text style={{ color: '#FFFFFF', fontWeight: 'bold', fontSize: 16 }}>Create Group Channel</Text>
          </TouchableOpacity>
        )}
      </SafeAreaView>
    );
  }

  // 3. Default Chat List Feed
  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.mainFeedHeader}>
        <Text style={styles.mainFeedTitle}>Platform Chats</Text>
        <TouchableOpacity 
          style={styles.newChatBtn}
          onPress={() => setShowNewChatModal(true)}
          activeOpacity={0.8}
        >
          <Text style={styles.newChatBtnText}>+ New</Text>
        </TouchableOpacity>
      </View>

      {chats.length === 0 ? (
        <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center', padding: 24 }}>
          <Text style={{ color: '#64748B', fontSize: 32, marginBottom: 12 }}>💬</Text>
          <Text style={{ color: '#94A3B8', fontSize: 15, fontWeight: 'bold', textAlign: 'center' }}>No active discussions</Text>
          <Text style={{ color: '#64748B', fontSize: 12, textAlign: 'center', marginTop: 4 }}>
            Start a direct discussion thread or create a group with your co-workers.
          </Text>
        </View>
      ) : (
        <FlatList
          data={chats}
          renderItem={renderChatItem}
          keyExtractor={item => item.id}
          contentContainerStyle={{ padding: 16 }}
        />
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.colors.background,
  },
  loadingContainer: { 
    flex: 1, 
    justifyContent: 'center', 
    alignItems: 'center', 
    backgroundColor: theme.colors.background 
  },
  mainFeedHeader: {
    flexDirection: 'row', 
    justifyContent: 'space-between', 
    alignItems: 'center', 
    padding: 16, 
    borderBottomWidth: 1, 
    borderBottomColor: theme.colors.border,
    backgroundColor: '#FFFFFF',
  },
  mainFeedTitle: { color: theme.colors.text, fontSize: 24, fontWeight: '800' },
  newChatBtn: { backgroundColor: theme.colors.primary, paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10 },
  newChatBtnText: { color: '#FFFFFF', fontWeight: 'bold', fontSize: 14 },
  
  chatCard: {
    backgroundColor: '#FFFFFF',
    borderRadius: 16,
    padding: 14,
    marginBottom: 10,
    flexDirection: 'row',
    alignItems: 'center',
    borderWidth: 1,
    borderColor: theme.colors.border,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.05,
    shadowRadius: 8,
    elevation: 2,
  },
  chatAvatar: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: '#E5E7EB',
    justifyContent: 'center',
    alignItems: 'center'
  },
  chatAvatarText: { fontSize: 20 },
  chatName: { color: theme.colors.text, fontSize: 16, fontWeight: '800' },
  chatSubtext: { color: theme.colors.textSecondary, fontSize: 12, marginTop: 2 },
  chatSecondaryText: { color: theme.colors.textSecondary, fontSize: 12 },
  chatArrow: { color: theme.colors.textSecondary, fontSize: 16, fontWeight: 'bold' },

  // Single Chat Header
  chatHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: theme.colors.border,
    backgroundColor: '#FFFFFF'
  },
  backBtn: { paddingVertical: 4 },
  chatHeaderDetails: { alignItems: 'center', flex: 1 },
  chatHeaderTitle: { color: theme.colors.text, fontSize: 17, fontWeight: '800' },

  // Message Bubbles
  messageRow: {
    marginBottom: 14,
    width: '100%',
  },
  messageRowMe: {
    alignItems: 'flex-end',
  },
  messageRowOther: {
    alignItems: 'flex-start',
  },
  messageSender: {
    color: theme.colors.primary,
    fontSize: 10,
    fontWeight: '800',
    marginBottom: 4,
    marginLeft: 6
  },
  messageBubble: {
    padding: 12,
    borderRadius: 16,
    maxWidth: '85%',
  },
  bubbleMe: {
    backgroundColor: theme.colors.primary,
    borderBottomRightRadius: 4,
  },
  bubbleOther: {
    backgroundColor: '#E5E7EB',
    borderBottomLeftRadius: 4,
    borderWidth: 1,
    borderColor: '#D1D5DB'
  },
  messageText: {
    fontSize: 14,
    lineHeight: 20,
  },
  textMe: { color: '#FFFFFF' },
  textOther: { color: theme.colors.text },
  messageTime: {
    fontSize: 9,
    marginTop: 6,
    color: theme.colors.textSecondary,
    textAlign: 'right',
  },

  // Audio Playback
  audioWrapper: {
    marginTop: 4,
    gap: 6
  },
  audioPlayButton: {
    backgroundColor: 'rgba(0, 0, 0, 0.05)',
    borderRadius: 10,
    paddingVertical: 10,
    paddingHorizontal: 12,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: 'rgba(0, 0, 0, 0.08)'
  },
  transcriptBox: {
    backgroundColor: 'rgba(0, 0, 0, 0.03)',
    borderRadius: 8,
    padding: 8,
    borderWidth: 1,
    borderColor: 'rgba(0, 0, 0, 0.05)'
  },
  transcriptText: {
    color: '#0284C7',
    fontSize: 12,
    fontWeight: '600',
    lineHeight: 16
  },

  // Input area
  inputPanel: {
    flexDirection: 'row',
    padding: 12,
    backgroundColor: '#FFFFFF',
    borderTopWidth: 1,
    borderTopColor: theme.colors.border,
    alignItems: 'center'
  },
  micBtn: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: theme.colors.primary,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 8
  },
  micBtnRecording: { backgroundColor: '#EF4444' },
  micBtnUploading: { backgroundColor: '#A855F7' },
  chatTextInput: {
    flex: 1,
    backgroundColor: '#F9FAFB',
    borderRadius: 12,
    paddingHorizontal: 16,
    height: 42,
    color: theme.colors.text,
    fontSize: 14,
    borderWidth: 1,
    borderColor: theme.colors.border
  },
  sendBtn: {
    backgroundColor: '#10B981',
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 12,
    marginLeft: 8,
    justifyContent: 'center',
    alignItems: 'center',
    height: 42
  },

  recordStatusBanner: {
    backgroundColor: 'rgba(239, 68, 68, 0.08)',
    padding: 10,
    alignItems: 'center',
    borderTopWidth: 1,
    borderTopColor: 'rgba(239, 68, 68, 0.15)'
  },
  recordStatusText: {
    color: '#EF4444',
    fontSize: 11,
    fontWeight: '800'
  },

  // Contacts / Selection Styles
  closeBtn: { padding: 4 },
  segmentedControl: {
    flexDirection: 'row',
    backgroundColor: '#E5E7EB',
    borderRadius: 12,
    padding: 4,
    marginBottom: 16
  },
  segmentBtn: {
    flex: 1,
    paddingVertical: 8,
    alignItems: 'center',
    borderRadius: 8
  },
  segmentBtnActive: {
    backgroundColor: theme.colors.primary
  },
  segmentText: { color: theme.colors.textSecondary, fontWeight: 'bold', fontSize: 13 },
  segmentTextActive: { color: '#FFFFFF' },
  inputLabel: {
    color: theme.colors.textSecondary,
    fontSize: 11,
    fontWeight: '800',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginBottom: 6
  },
  groupInput: {
    backgroundColor: '#FFFFFF',
    borderRadius: 10,
    paddingHorizontal: 12,
    height: 44,
    color: theme.colors.text,
    fontSize: 14,
    borderWidth: 1,
    borderColor: theme.colors.border,
    marginBottom: 12
  },
  contactCard: {
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    padding: 12,
    marginBottom: 8,
    flexDirection: 'row',
    alignItems: 'center',
    borderWidth: 1,
    borderColor: theme.colors.border
  },
  contactCardSelected: {
    borderColor: theme.colors.primary,
    backgroundColor: 'rgba(59, 130, 246, 0.05)'
  },
  contactAvatar: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: '#E5E7EB',
    justifyContent: 'center',
    alignItems: 'center'
  },
  contactName: { color: theme.colors.text, fontSize: 14, fontWeight: '700' },
  checkbox: {
    width: 20,
    height: 20,
    borderRadius: 4,
    borderWidth: 2,
    borderColor: theme.colors.border,
    justifyContent: 'center',
    alignItems: 'center'
  },
  checkboxSelected: {
    borderColor: theme.colors.primary,
    backgroundColor: theme.colors.primary
  },
  groupSubmitBtn: {
    backgroundColor: '#10B981',
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: 'center',
    marginTop: 16
  }
});
