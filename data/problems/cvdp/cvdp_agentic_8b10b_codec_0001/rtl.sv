module decoder_8b10b (
    input logic clk_in, reset_in, control_in,
    input logic [9:0] decoder_in,
    input logic decoder_valid_in,
    output logic [7:0] decoder_out,
    output logic decoder_valid_out, control_out
);
    function automatic logic [7:0] control_decode(input logic [9:0] code);
        control_decode=0;
        case(code)
            10'd87: control_decode=8'd247;
            10'd151: control_decode=8'd251;
            10'd242: control_decode=8'd156;
            10'd243: control_decode=8'd124;
            10'd244: control_decode=8'd28;
            10'd245: control_decode=8'd92;
            10'd246: control_decode=8'd220;
            10'd248: control_decode=8'd252;
            10'd249: control_decode=8'd60;
            10'd250: control_decode=8'd188;
            10'd279: control_decode=8'd253;
            10'd488: control_decode=8'd254;
            10'd535: control_decode=8'd254;
            10'd744: control_decode=8'd253;
            10'd773: control_decode=8'd188;
            10'd774: control_decode=8'd60;
            10'd775: control_decode=8'd252;
            10'd777: control_decode=8'd220;
            10'd778: control_decode=8'd92;
            10'd779: control_decode=8'd28;
            10'd780: control_decode=8'd124;
            10'd781: control_decode=8'd156;
            10'd872: control_decode=8'd251;
            10'd936: control_decode=8'd247;
        endcase
    endfunction
    function automatic logic [4:0] six_decode(input logic [5:0] code);
        six_decode=0;
        case(code)
            6'd5: six_decode=5'd23;
            6'd6: six_decode=5'd8;
            6'd7: six_decode=5'd7;
            6'd9: six_decode=5'd27;
            6'd10: six_decode=5'd4;
            6'd11: six_decode=5'd20;
            6'd12: six_decode=5'd24;
            6'd13: six_decode=5'd12;
            6'd14: six_decode=5'd28;
            6'd17: six_decode=5'd29;
            6'd18: six_decode=5'd2;
            6'd19: six_decode=5'd18;
            6'd20: six_decode=5'd31;
            6'd21: six_decode=5'd10;
            6'd22: six_decode=5'd26;
            6'd23: six_decode=5'd15;
            6'd24: six_decode=5'd0;
            6'd25: six_decode=5'd6;
            6'd26: six_decode=5'd22;
            6'd27: six_decode=5'd16;
            6'd28: six_decode=5'd14;
            6'd29: six_decode=5'd1;
            6'd30: six_decode=5'd30;
            6'd33: six_decode=5'd30;
            6'd34: six_decode=5'd1;
            6'd35: six_decode=5'd17;
            6'd36: six_decode=5'd16;
            6'd37: six_decode=5'd9;
            6'd38: six_decode=5'd25;
            6'd39: six_decode=5'd0;
            6'd40: six_decode=5'd15;
            6'd41: six_decode=5'd5;
            6'd42: six_decode=5'd21;
            6'd43: six_decode=5'd31;
            6'd44: six_decode=5'd13;
            6'd45: six_decode=5'd2;
            6'd46: six_decode=5'd29;
            6'd49: six_decode=5'd3;
            6'd50: six_decode=5'd19;
            6'd51: six_decode=5'd24;
            6'd52: six_decode=5'd11;
            6'd53: six_decode=5'd4;
            6'd54: six_decode=5'd27;
            6'd56: six_decode=5'd7;
            6'd57: six_decode=5'd8;
            6'd58: six_decode=5'd23;
        endcase
    endfunction
    function automatic logic [2:0] four_decode(input logic [3:0] code);
        four_decode=0;
        case(code)
            4'd1: four_decode=3'd7;
            4'd2: four_decode=3'd4;
            4'd3: four_decode=3'd3;
            4'd4: four_decode=3'd0;
            4'd5: four_decode=3'd2;
            4'd6: four_decode=3'd6;
            4'd9: four_decode=3'd1;
            4'd10: four_decode=3'd5;
            4'd11: four_decode=3'd0;
            4'd12: four_decode=3'd3;
            4'd13: four_decode=3'd4;
            4'd14: four_decode=3'd7;
        endcase
    endfunction
    always_ff @(posedge clk_in or posedge reset_in) begin
        if(reset_in) begin decoder_out<=0; decoder_valid_out<=0; control_out<=0; end
        else begin
            decoder_valid_out<=decoder_valid_in;
            if(decoder_valid_in) begin
                control_out<=control_in;
                decoder_out<=control_in ? control_decode(decoder_in) : {four_decode(decoder_in[3:0]),six_decode(decoder_in[9:4])};
            end
        end
    end
endmodule
