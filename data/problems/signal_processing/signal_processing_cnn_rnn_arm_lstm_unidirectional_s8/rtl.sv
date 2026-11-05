// CMSIS-NN v7.0.0 arithmetic; see SOURCE_LICENSE (Apache-2.0).
module cmsis_s8_lstm (
    input logic clk, rst,
    input logic in_valid, output logic in_ready,
    input logic in_time_major,
    input logic [95:0] in_input_data,
    input logic [127:0] in_input_weights, in_hidden_weights, in_bias,
    output logic out_valid, input logic out_ready,
    output logic [7:0] out_status,
    output logic [95:0] out_output_data,
    output logic [63:0] out_cell_state
);
localparam logic [4095:0] LUT = {
16'd65535, 16'd65534, 16'd65534, 16'd65534, 16'd65534, 16'd65534, 16'd65534, 16'd65534, 16'd65534, 16'd65534, 16'd65534, 16'd65533, 16'd65533, 16'd65533, 16'd65533, 16'd65533, 16'd65533, 16'd65533, 16'd65533, 16'd65532, 16'd65532, 16'd65532, 16'd65532, 16'd65532, 16'd65532, 16'd65531, 16'd65531, 16'd65531, 16'd65531, 16'd65531, 16'd65530, 16'd65530, 16'd65530, 16'd65530, 16'd65529, 16'd65529, 16'd65529, 16'd65529, 16'd65528, 16'd65528, 16'd65528, 16'd65527, 16'd65527, 16'd65526, 16'd65526, 16'd65526, 16'd65525, 16'd65525, 16'd65524, 16'd65524, 16'd65523, 16'd65523, 16'd65522, 16'd65522, 16'd65521, 16'd65520, 16'd65520, 16'd65519, 16'd65518, 16'd65517, 16'd65517, 16'd65516, 16'd65515, 16'd65514, 16'd65513, 16'd65512, 16'd65511, 16'd65510, 16'd65509, 16'd65508, 16'd65507, 16'd65505, 16'd65504, 16'd65503, 16'd65501, 16'd65500, 16'd65498, 16'd65497, 16'd65495, 16'd65493, 16'd65491, 16'd65489, 16'd65488, 16'd65485, 16'd65483, 16'd65481, 16'd65479, 16'd65476, 16'd65474, 16'd65471, 16'd65468, 16'd65465, 16'd65462, 16'd65459, 16'd65456, 16'd65453, 16'd65449, 16'd65445, 16'd65442, 16'd65438, 16'd65433, 16'd65429, 16'd65425, 16'd65420, 16'd65415, 16'd65410, 16'd65404, 16'd65399, 16'd65393, 16'd65387, 16'd65381, 16'd65374, 16'd65367, 16'd65360, 16'd65352, 16'd65345, 16'd65337, 16'd65328, 16'd65319, 16'd65310, 16'd65301, 16'd65291, 16'd65280, 16'd65269, 16'd65258, 16'd65246, 16'd65234, 16'd65221, 16'd65208, 16'd65194, 16'd65179, 16'd65164, 16'd65149, 16'd65132, 16'd65115, 16'd65097, 16'd65079, 16'd65060, 16'd65039, 16'd65019, 16'd64997, 16'd64974, 16'd64950, 16'd64926, 16'd64900, 16'd64873, 16'd64845, 16'd64816, 16'd64786, 16'd64754, 16'd64721, 16'd64687, 16'd64652, 16'd64614, 16'd64576, 16'd64536, 16'd64494, 16'd64450, 16'd64405, 16'd64357, 16'd64308, 16'd64257, 16'd64204, 16'd64148, 16'd64090, 16'd64030, 16'd63968, 16'd63903, 16'd63835, 16'd63765, 16'd63691, 16'd63615, 16'd63536, 16'd63454, 16'd63368, 16'd63279, 16'd63186, 16'd63090, 16'd62990, 16'd62886, 16'd62778, 16'd62666, 16'd62549, 16'd62428, 16'd62302, 16'd62172, 16'd62036, 16'd61896, 16'd61750, 16'd61599, 16'd61441, 16'd61279, 16'd61110, 16'd60935, 16'd60753, 16'd60565, 16'd60370, 16'd60168, 16'd59959, 16'd59743, 16'd59519, 16'd59288, 16'd59048, 16'd58800, 16'd58544, 16'd58280, 16'd58007, 16'd57724, 16'd57433, 16'd57133, 16'd56823, 16'd56503, 16'd56174, 16'd55834, 16'd55485, 16'd55125, 16'd54755, 16'd54374, 16'd53983, 16'd53581, 16'd53169, 16'd52745, 16'd52311, 16'd51865, 16'd51409, 16'd50942, 16'd50464, 16'd49975, 16'd49475, 16'd48964, 16'd48443, 16'd47911, 16'd47369, 16'd46817, 16'd46255, 16'd45683, 16'd45102, 16'd44511, 16'd43912, 16'd43304, 16'd42688, 16'd42064, 16'd41432, 16'd40794, 16'd40149, 16'd39498, 16'd38841, 16'd38180, 16'd37513, 16'd36843, 16'd36169, 16'd35493, 16'd34813, 16'd34133, 16'd33451, 16'd32768
};
function automatic integer sat16(input integer x);
    if(x>32767)sat16=32767;else if(x < -32768)sat16=-32768;else sat16=x;
endfunction
function automatic integer requant(input integer x,input integer shift);
    integer v;
    begin v=(x+1)>>>1;
        if(v<0)requant=-(((-v)+(1<<(shift-1)))>>>shift);
        else requant=(v+(1<<(shift-1)))>>>shift;
    end
endfunction
function automatic integer activation(input integer x,input logic is_tanh);
    integer scaled,mag,bits,idx,value,lo,hi;
    begin
        scaled=x*3;mag=scaled<0 ? -scaled:scaled;bits=is_tanh ? 8:9;idx=mag>>>bits;
        if(idx>=255)value=is_tanh ? (65535<<8):(32767<<10);
        else begin
            lo=LUT[idx*16+:16];hi=LUT[(idx+1)*16+:16];
            value=(lo<<bits)+(mag&((1<<bits)-1))*(hi-lo);
        end
        if(!is_tanh)begin
            if(scaled>=0)value=value+512;else value=(1<<25)-value+511;
            value=value>>>10;
        end else begin
            if(scaled>=0)value=value-(1<<23)+128;else value=(1<<23)-value+127;
            value=value>>>8;
        end
        activation=$signed(value[15:0]);
    end
endfunction
integer h[0:3],cells[0:3],gates[0:7];
integer t,b,g,j,k,base,acc,recur,previous,updated,new_h;
logic [95:0] y;
logic [63:0] cfinal;
always_comb begin
    y=0;cfinal=0;acc=0;recur=0;previous=0;updated=0;new_h=0;base=0;
    for(j=0;j<4;j=j+1)begin h[j]=0;cells[j]=0;end
    for(j=0;j<8;j=j+1)gates[j]=0;
    for(t=0;t<3;t=t+1)for(b=0;b<2;b=b+1)begin
        base=in_time_major ? (t*2+b)*2:(b*3+t)*2;
        for(g=0;g<4;g=g+1)for(j=0;j<2;j=j+1)begin
            acc=$signed(in_bias[(g*2+j)*16+:16]);recur=0;
            for(k=0;k<2;k=k+1)begin
                acc=acc+32'($signed(in_input_data[(base+k)*8+:8]))*32'($signed(in_input_weights[(g*4+j*2+k)*8+:8]));
                recur=recur+h[b*2+k]*32'($signed(in_hidden_weights[(g*4+j*2+k)*8+:8]));
            end
            gates[g*2+j]=activation(sat16(sat16(acc)+recur),g==2);
        end
        for(j=0;j<2;j=j+1)begin
            previous=sat16(requant(gates[j]*cells[b*2+j],14));
            updated=previous+requant(gates[2+j]*gates[4+j],17);
            if(updated>32767)updated=32767;else if(updated < -32767)updated=-32767;
            cells[b*2+j]=updated;
            new_h=requant(gates[6+j]*activation(updated,1),22);
            if(new_h>127)new_h=127;else if(new_h < -128)new_h=-128;
            h[b*2+j]=new_h;y[(base+j)*8+:8]=new_h;
        end
    end
    for(j=0;j<4;j=j+1)cfinal[j*16+:16]=cells[j];
end
assign in_ready=!out_valid || out_ready;
assign out_status=0;
always_ff @(posedge clk)begin
    if(rst)begin out_valid<=0;out_output_data<=0;out_cell_state<=0;end
    else if(in_ready)begin
        out_valid<=in_valid;out_output_data<=in_valid ? y:0;out_cell_state<=in_valid ? cfinal:0;
    end
end
endmodule
