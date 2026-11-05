// SPDX-License-Identifier: Apache-2.0 AND Unlicense
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module AESCMACShort31 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_key,
    input logic [247:0] in_message,
    input logic [4:0] in_length,
    output logic [127:0] out_values
);


function automatic [7:0] aes_sbox(input logic [7:0] x);
  begin
    case(x)
      8'd0: aes_sbox=8'h63;
      8'd1: aes_sbox=8'h7c;
      8'd2: aes_sbox=8'h77;
      8'd3: aes_sbox=8'h7b;
      8'd4: aes_sbox=8'hf2;
      8'd5: aes_sbox=8'h6b;
      8'd6: aes_sbox=8'h6f;
      8'd7: aes_sbox=8'hc5;
      8'd8: aes_sbox=8'h30;
      8'd9: aes_sbox=8'h01;
      8'd10: aes_sbox=8'h67;
      8'd11: aes_sbox=8'h2b;
      8'd12: aes_sbox=8'hfe;
      8'd13: aes_sbox=8'hd7;
      8'd14: aes_sbox=8'hab;
      8'd15: aes_sbox=8'h76;
      8'd16: aes_sbox=8'hca;
      8'd17: aes_sbox=8'h82;
      8'd18: aes_sbox=8'hc9;
      8'd19: aes_sbox=8'h7d;
      8'd20: aes_sbox=8'hfa;
      8'd21: aes_sbox=8'h59;
      8'd22: aes_sbox=8'h47;
      8'd23: aes_sbox=8'hf0;
      8'd24: aes_sbox=8'had;
      8'd25: aes_sbox=8'hd4;
      8'd26: aes_sbox=8'ha2;
      8'd27: aes_sbox=8'haf;
      8'd28: aes_sbox=8'h9c;
      8'd29: aes_sbox=8'ha4;
      8'd30: aes_sbox=8'h72;
      8'd31: aes_sbox=8'hc0;
      8'd32: aes_sbox=8'hb7;
      8'd33: aes_sbox=8'hfd;
      8'd34: aes_sbox=8'h93;
      8'd35: aes_sbox=8'h26;
      8'd36: aes_sbox=8'h36;
      8'd37: aes_sbox=8'h3f;
      8'd38: aes_sbox=8'hf7;
      8'd39: aes_sbox=8'hcc;
      8'd40: aes_sbox=8'h34;
      8'd41: aes_sbox=8'ha5;
      8'd42: aes_sbox=8'he5;
      8'd43: aes_sbox=8'hf1;
      8'd44: aes_sbox=8'h71;
      8'd45: aes_sbox=8'hd8;
      8'd46: aes_sbox=8'h31;
      8'd47: aes_sbox=8'h15;
      8'd48: aes_sbox=8'h04;
      8'd49: aes_sbox=8'hc7;
      8'd50: aes_sbox=8'h23;
      8'd51: aes_sbox=8'hc3;
      8'd52: aes_sbox=8'h18;
      8'd53: aes_sbox=8'h96;
      8'd54: aes_sbox=8'h05;
      8'd55: aes_sbox=8'h9a;
      8'd56: aes_sbox=8'h07;
      8'd57: aes_sbox=8'h12;
      8'd58: aes_sbox=8'h80;
      8'd59: aes_sbox=8'he2;
      8'd60: aes_sbox=8'heb;
      8'd61: aes_sbox=8'h27;
      8'd62: aes_sbox=8'hb2;
      8'd63: aes_sbox=8'h75;
      8'd64: aes_sbox=8'h09;
      8'd65: aes_sbox=8'h83;
      8'd66: aes_sbox=8'h2c;
      8'd67: aes_sbox=8'h1a;
      8'd68: aes_sbox=8'h1b;
      8'd69: aes_sbox=8'h6e;
      8'd70: aes_sbox=8'h5a;
      8'd71: aes_sbox=8'ha0;
      8'd72: aes_sbox=8'h52;
      8'd73: aes_sbox=8'h3b;
      8'd74: aes_sbox=8'hd6;
      8'd75: aes_sbox=8'hb3;
      8'd76: aes_sbox=8'h29;
      8'd77: aes_sbox=8'he3;
      8'd78: aes_sbox=8'h2f;
      8'd79: aes_sbox=8'h84;
      8'd80: aes_sbox=8'h53;
      8'd81: aes_sbox=8'hd1;
      8'd82: aes_sbox=8'h00;
      8'd83: aes_sbox=8'hed;
      8'd84: aes_sbox=8'h20;
      8'd85: aes_sbox=8'hfc;
      8'd86: aes_sbox=8'hb1;
      8'd87: aes_sbox=8'h5b;
      8'd88: aes_sbox=8'h6a;
      8'd89: aes_sbox=8'hcb;
      8'd90: aes_sbox=8'hbe;
      8'd91: aes_sbox=8'h39;
      8'd92: aes_sbox=8'h4a;
      8'd93: aes_sbox=8'h4c;
      8'd94: aes_sbox=8'h58;
      8'd95: aes_sbox=8'hcf;
      8'd96: aes_sbox=8'hd0;
      8'd97: aes_sbox=8'hef;
      8'd98: aes_sbox=8'haa;
      8'd99: aes_sbox=8'hfb;
      8'd100: aes_sbox=8'h43;
      8'd101: aes_sbox=8'h4d;
      8'd102: aes_sbox=8'h33;
      8'd103: aes_sbox=8'h85;
      8'd104: aes_sbox=8'h45;
      8'd105: aes_sbox=8'hf9;
      8'd106: aes_sbox=8'h02;
      8'd107: aes_sbox=8'h7f;
      8'd108: aes_sbox=8'h50;
      8'd109: aes_sbox=8'h3c;
      8'd110: aes_sbox=8'h9f;
      8'd111: aes_sbox=8'ha8;
      8'd112: aes_sbox=8'h51;
      8'd113: aes_sbox=8'ha3;
      8'd114: aes_sbox=8'h40;
      8'd115: aes_sbox=8'h8f;
      8'd116: aes_sbox=8'h92;
      8'd117: aes_sbox=8'h9d;
      8'd118: aes_sbox=8'h38;
      8'd119: aes_sbox=8'hf5;
      8'd120: aes_sbox=8'hbc;
      8'd121: aes_sbox=8'hb6;
      8'd122: aes_sbox=8'hda;
      8'd123: aes_sbox=8'h21;
      8'd124: aes_sbox=8'h10;
      8'd125: aes_sbox=8'hff;
      8'd126: aes_sbox=8'hf3;
      8'd127: aes_sbox=8'hd2;
      8'd128: aes_sbox=8'hcd;
      8'd129: aes_sbox=8'h0c;
      8'd130: aes_sbox=8'h13;
      8'd131: aes_sbox=8'hec;
      8'd132: aes_sbox=8'h5f;
      8'd133: aes_sbox=8'h97;
      8'd134: aes_sbox=8'h44;
      8'd135: aes_sbox=8'h17;
      8'd136: aes_sbox=8'hc4;
      8'd137: aes_sbox=8'ha7;
      8'd138: aes_sbox=8'h7e;
      8'd139: aes_sbox=8'h3d;
      8'd140: aes_sbox=8'h64;
      8'd141: aes_sbox=8'h5d;
      8'd142: aes_sbox=8'h19;
      8'd143: aes_sbox=8'h73;
      8'd144: aes_sbox=8'h60;
      8'd145: aes_sbox=8'h81;
      8'd146: aes_sbox=8'h4f;
      8'd147: aes_sbox=8'hdc;
      8'd148: aes_sbox=8'h22;
      8'd149: aes_sbox=8'h2a;
      8'd150: aes_sbox=8'h90;
      8'd151: aes_sbox=8'h88;
      8'd152: aes_sbox=8'h46;
      8'd153: aes_sbox=8'hee;
      8'd154: aes_sbox=8'hb8;
      8'd155: aes_sbox=8'h14;
      8'd156: aes_sbox=8'hde;
      8'd157: aes_sbox=8'h5e;
      8'd158: aes_sbox=8'h0b;
      8'd159: aes_sbox=8'hdb;
      8'd160: aes_sbox=8'he0;
      8'd161: aes_sbox=8'h32;
      8'd162: aes_sbox=8'h3a;
      8'd163: aes_sbox=8'h0a;
      8'd164: aes_sbox=8'h49;
      8'd165: aes_sbox=8'h06;
      8'd166: aes_sbox=8'h24;
      8'd167: aes_sbox=8'h5c;
      8'd168: aes_sbox=8'hc2;
      8'd169: aes_sbox=8'hd3;
      8'd170: aes_sbox=8'hac;
      8'd171: aes_sbox=8'h62;
      8'd172: aes_sbox=8'h91;
      8'd173: aes_sbox=8'h95;
      8'd174: aes_sbox=8'he4;
      8'd175: aes_sbox=8'h79;
      8'd176: aes_sbox=8'he7;
      8'd177: aes_sbox=8'hc8;
      8'd178: aes_sbox=8'h37;
      8'd179: aes_sbox=8'h6d;
      8'd180: aes_sbox=8'h8d;
      8'd181: aes_sbox=8'hd5;
      8'd182: aes_sbox=8'h4e;
      8'd183: aes_sbox=8'ha9;
      8'd184: aes_sbox=8'h6c;
      8'd185: aes_sbox=8'h56;
      8'd186: aes_sbox=8'hf4;
      8'd187: aes_sbox=8'hea;
      8'd188: aes_sbox=8'h65;
      8'd189: aes_sbox=8'h7a;
      8'd190: aes_sbox=8'hae;
      8'd191: aes_sbox=8'h08;
      8'd192: aes_sbox=8'hba;
      8'd193: aes_sbox=8'h78;
      8'd194: aes_sbox=8'h25;
      8'd195: aes_sbox=8'h2e;
      8'd196: aes_sbox=8'h1c;
      8'd197: aes_sbox=8'ha6;
      8'd198: aes_sbox=8'hb4;
      8'd199: aes_sbox=8'hc6;
      8'd200: aes_sbox=8'he8;
      8'd201: aes_sbox=8'hdd;
      8'd202: aes_sbox=8'h74;
      8'd203: aes_sbox=8'h1f;
      8'd204: aes_sbox=8'h4b;
      8'd205: aes_sbox=8'hbd;
      8'd206: aes_sbox=8'h8b;
      8'd207: aes_sbox=8'h8a;
      8'd208: aes_sbox=8'h70;
      8'd209: aes_sbox=8'h3e;
      8'd210: aes_sbox=8'hb5;
      8'd211: aes_sbox=8'h66;
      8'd212: aes_sbox=8'h48;
      8'd213: aes_sbox=8'h03;
      8'd214: aes_sbox=8'hf6;
      8'd215: aes_sbox=8'h0e;
      8'd216: aes_sbox=8'h61;
      8'd217: aes_sbox=8'h35;
      8'd218: aes_sbox=8'h57;
      8'd219: aes_sbox=8'hb9;
      8'd220: aes_sbox=8'h86;
      8'd221: aes_sbox=8'hc1;
      8'd222: aes_sbox=8'h1d;
      8'd223: aes_sbox=8'h9e;
      8'd224: aes_sbox=8'he1;
      8'd225: aes_sbox=8'hf8;
      8'd226: aes_sbox=8'h98;
      8'd227: aes_sbox=8'h11;
      8'd228: aes_sbox=8'h69;
      8'd229: aes_sbox=8'hd9;
      8'd230: aes_sbox=8'h8e;
      8'd231: aes_sbox=8'h94;
      8'd232: aes_sbox=8'h9b;
      8'd233: aes_sbox=8'h1e;
      8'd234: aes_sbox=8'h87;
      8'd235: aes_sbox=8'he9;
      8'd236: aes_sbox=8'hce;
      8'd237: aes_sbox=8'h55;
      8'd238: aes_sbox=8'h28;
      8'd239: aes_sbox=8'hdf;
      8'd240: aes_sbox=8'h8c;
      8'd241: aes_sbox=8'ha1;
      8'd242: aes_sbox=8'h89;
      8'd243: aes_sbox=8'h0d;
      8'd244: aes_sbox=8'hbf;
      8'd245: aes_sbox=8'he6;
      8'd246: aes_sbox=8'h42;
      8'd247: aes_sbox=8'h68;
      8'd248: aes_sbox=8'h41;
      8'd249: aes_sbox=8'h99;
      8'd250: aes_sbox=8'h2d;
      8'd251: aes_sbox=8'h0f;
      8'd252: aes_sbox=8'hb0;
      8'd253: aes_sbox=8'h54;
      8'd254: aes_sbox=8'hbb;
      8'd255: aes_sbox=8'h16;
      default: aes_sbox=0;
    endcase
  end
endfunction
function automatic [7:0] aes_xtime(input logic [7:0] x);
  aes_xtime = {x[6:0],1'b0} ^ (x[7] ? 8'h1b : 8'h00);
endfunction

function automatic [127:0] aes128(input logic [127:0] in_key,in_plaintext);
  logic [127:0] result;
  begin
    begin : cipher
      logic [7:0] keys[0:175], state[0:15], shifted[0:15];
      logic [7:0] a,b,c,d,t0,t1,t2,t3,rc;
      a=0; b=0; c=0; d=0; t0=0; t1=0; t2=0; t3=0; rc=1;
      for(integer i=0;i<176;i=i+1) keys[i]=0;
      for(integer i=0;i<16;i=i+1) begin
        keys[i]=in_key[8*i +: 8];
        state[i]=in_plaintext[8*i +: 8]^keys[i]; shifted[i]=0;
      end
      for(integer word_index=4;word_index<44;word_index=word_index+1) begin
        t0=keys[4*word_index-4]; t1=keys[4*word_index-3];
        t2=keys[4*word_index-2]; t3=keys[4*word_index-1];
        if(word_index%4==0) begin
          a=aes_sbox(t1)^rc; b=aes_sbox(t2); c=aes_sbox(t3); d=aes_sbox(t0);
          t0=a; t1=b; t2=c; t3=d; rc=aes_xtime(rc);
        end
        keys[4*word_index]=keys[4*word_index-16]^t0;
        keys[4*word_index+1]=keys[4*word_index-15]^t1;
        keys[4*word_index+2]=keys[4*word_index-14]^t2;
        keys[4*word_index+3]=keys[4*word_index-13]^t3;
      end
      for(integer round_index=1;round_index<=10;round_index=round_index+1) begin
        for(integer col=0;col<4;col=col+1)
          for(integer row=0;row<4;row=row+1)
            shifted[4*col+row]=aes_sbox(state[4*((col+row)%4)+row]);
        for(integer col=0;col<4;col=col+1) begin
          a=shifted[4*col]; b=shifted[4*col+1]; c=shifted[4*col+2]; d=shifted[4*col+3];
          if(round_index<10) begin
            state[4*col]=aes_xtime(a)^aes_xtime(b)^b^c^d;
            state[4*col+1]=a^aes_xtime(b)^aes_xtime(c)^c^d;
            state[4*col+2]=a^b^aes_xtime(c)^aes_xtime(d)^d;
            state[4*col+3]=aes_xtime(a)^a^b^c^aes_xtime(d);
          end else begin
            state[4*col]=a; state[4*col+1]=b; state[4*col+2]=c; state[4*col+3]=d;
          end
        end
        for(integer i=0;i<16;i=i+1) state[i]=state[i]^keys[16*round_index+i];
      end
      for(integer i=0;i<16;i=i+1) result[8*i +: 8]=state[i];
    end
    aes128=result;
  end
endfunction
function automatic [127:0] double_block(input logic [127:0] x);
  logic [127:0] answer;
  begin
    answer=0;
    for(integer i=0;i<16;i=i+1) begin
      answer[8*i +: 8]=x[8*i +: 8]<<1;
      if(i<15) answer[8*i]=x[8*(i+1)+7];
    end
    if(x[7]) answer[120 +: 8]=answer[120 +: 8]^8'h87;
    double_block=answer;
  end
endfunction

logic [127:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : cmac
    logic [127:0] k1,k2,chain,block_value;integer offset,count;
    k1=double_block(aes128(in_key,128'd0));k2=double_block(k1);chain=0;offset=0;
    if(in_length>16) begin chain=aes128(in_key,in_message[127:0]);offset=16;end
    count=in_length-offset;block_value=0;
    for(integer j=0;j<16;j=j+1) if(j<count) block_value[8*j +: 8]=in_message >> (8*(offset+j));
    if(count==16) block_value=block_value^k1;
    else begin block_value[8*count +: 8]=8'h80;block_value=block_value^k2;end
    result=aes128(in_key,chain^block_value);
  end
end
assign in_ready = !out_valid || out_ready;
always_ff @(posedge clk) begin
  if (rst) begin
    out_valid <= 0;
    out_values <= 0;

  end else if (in_ready) begin
    out_valid <= in_valid;
    if (in_valid) out_values <= result;

  end
end
endmodule
