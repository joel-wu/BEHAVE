module cryptography_pqc_kyber_kyber_invntt_butterfly (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_twiddle_index,
    input  logic [15:0] in_a,
    input  logic [15:0] in_b,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_a,
    output logic [15:0] out_b
);

logic busy;
wire  output_consumed;
wire  input_accepted;

assign output_consumed = out_valid & out_ready;
assign input_accepted  = in_valid & in_ready;
assign in_ready        = ~busy | output_consumed;
assign out_valid       = busy;

function automatic logic signed [15:0] zeta_lookup(input logic [6:0] idx);
    begin
        zeta_lookup = 16'sd0;
        case (idx)
            7'd0:   zeta_lookup = -16'sd1044;
            7'd1:   zeta_lookup = -16'sd758;
            7'd2:   zeta_lookup = -16'sd359;
            7'd3:   zeta_lookup = -16'sd1517;
            7'd4:   zeta_lookup =  16'sd1493;
            7'd5:   zeta_lookup =  16'sd1422;
            7'd6:   zeta_lookup =  16'sd287;
            7'd7:   zeta_lookup =  16'sd202;
            7'd8:   zeta_lookup = -16'sd171;
            7'd9:   zeta_lookup =  16'sd622;
            7'd10:  zeta_lookup =  16'sd1577;
            7'd11:  zeta_lookup =  16'sd182;
            7'd12:  zeta_lookup =  16'sd962;
            7'd13:  zeta_lookup = -16'sd1202;
            7'd14:  zeta_lookup = -16'sd1474;
            7'd15:  zeta_lookup =  16'sd1468;
            7'd16:  zeta_lookup =  16'sd573;
            7'd17:  zeta_lookup = -16'sd1325;
            7'd18:  zeta_lookup =  16'sd264;
            7'd19:  zeta_lookup =  16'sd383;
            7'd20:  zeta_lookup = -16'sd829;
            7'd21:  zeta_lookup =  16'sd1458;
            7'd22:  zeta_lookup = -16'sd1602;
            7'd23:  zeta_lookup = -16'sd130;
            7'd24:  zeta_lookup = -16'sd681;
            7'd25:  zeta_lookup =  16'sd1017;
            7'd26:  zeta_lookup =  16'sd732;
            7'd27:  zeta_lookup =  16'sd608;
            7'd28:  zeta_lookup = -16'sd1542;
            7'd29:  zeta_lookup =  16'sd411;
            7'd30:  zeta_lookup = -16'sd205;
            7'd31:  zeta_lookup = -16'sd1571;
            7'd32:  zeta_lookup =  16'sd1223;
            7'd33:  zeta_lookup =  16'sd652;
            7'd34:  zeta_lookup = -16'sd552;
            7'd35:  zeta_lookup =  16'sd1015;
            7'd36:  zeta_lookup = -16'sd1293;
            7'd37:  zeta_lookup =  16'sd1491;
            7'd38:  zeta_lookup = -16'sd282;
            7'd39:  zeta_lookup = -16'sd1544;
            7'd40:  zeta_lookup =  16'sd516;
            7'd41:  zeta_lookup = -16'sd8;
            7'd42:  zeta_lookup = -16'sd320;
            7'd43:  zeta_lookup = -16'sd666;
            7'd44:  zeta_lookup = -16'sd1618;
            7'd45:  zeta_lookup = -16'sd1162;
            7'd46:  zeta_lookup =  16'sd126;
            7'd47:  zeta_lookup =  16'sd1469;
            7'd48:  zeta_lookup = -16'sd853;
            7'd49:  zeta_lookup = -16'sd90;
            7'd50:  zeta_lookup = -16'sd271;
            7'd51:  zeta_lookup =  16'sd830;
            7'd52:  zeta_lookup =  16'sd107;
            7'd53:  zeta_lookup = -16'sd1421;
            7'd54:  zeta_lookup = -16'sd247;
            7'd55:  zeta_lookup = -16'sd951;
            7'd56:  zeta_lookup = -16'sd398;
            7'd57:  zeta_lookup =  16'sd961;
            7'd58:  zeta_lookup = -16'sd1508;
            7'd59:  zeta_lookup = -16'sd725;
            7'd60:  zeta_lookup =  16'sd448;
            7'd61:  zeta_lookup = -16'sd1065;
            7'd62:  zeta_lookup =  16'sd677;
            7'd63:  zeta_lookup = -16'sd1275;
            7'd64:  zeta_lookup = -16'sd1103;
            7'd65:  zeta_lookup =  16'sd430;
            7'd66:  zeta_lookup =  16'sd555;
            7'd67:  zeta_lookup =  16'sd843;
            7'd68:  zeta_lookup = -16'sd1251;
            7'd69:  zeta_lookup =  16'sd871;
            7'd70:  zeta_lookup =  16'sd1550;
            7'd71:  zeta_lookup =  16'sd105;
            7'd72:  zeta_lookup =  16'sd422;
            7'd73:  zeta_lookup =  16'sd587;
            7'd74:  zeta_lookup =  16'sd177;
            7'd75:  zeta_lookup = -16'sd235;
            7'd76:  zeta_lookup = -16'sd291;
            7'd77:  zeta_lookup = -16'sd460;
            7'd78:  zeta_lookup =  16'sd1574;
            7'd79:  zeta_lookup =  16'sd1653;
            7'd80:  zeta_lookup = -16'sd246;
            7'd81:  zeta_lookup =  16'sd778;
            7'd82:  zeta_lookup =  16'sd1159;
            7'd83:  zeta_lookup = -16'sd147;
            7'd84:  zeta_lookup = -16'sd777;
            7'd85:  zeta_lookup =  16'sd1483;
            7'd86:  zeta_lookup = -16'sd602;
            7'd87:  zeta_lookup =  16'sd1119;
            7'd88:  zeta_lookup = -16'sd1590;
            7'd89:  zeta_lookup =  16'sd644;
            7'd90:  zeta_lookup = -16'sd872;
            7'd91:  zeta_lookup =  16'sd349;
            7'd92:  zeta_lookup =  16'sd418;
            7'd93:  zeta_lookup =  16'sd329;
            7'd94:  zeta_lookup = -16'sd156;
            7'd95:  zeta_lookup = -16'sd75;
            7'd96:  zeta_lookup =  16'sd817;
            7'd97:  zeta_lookup =  16'sd1097;
            7'd98:  zeta_lookup =  16'sd603;
            7'd99:  zeta_lookup =  16'sd610;
            7'd100: zeta_lookup =  16'sd1322;
            7'd101: zeta_lookup = -16'sd1285;
            7'd102: zeta_lookup = -16'sd1465;
            7'd103: zeta_lookup =  16'sd384;
            7'd104: zeta_lookup = -16'sd1215;
            7'd105: zeta_lookup = -16'sd136;
            7'd106: zeta_lookup =  16'sd1218;
            7'd107: zeta_lookup = -16'sd1335;
            7'd108: zeta_lookup = -16'sd874;
            7'd109: zeta_lookup =  16'sd220;
            7'd110: zeta_lookup = -16'sd1187;
            7'd111: zeta_lookup = -16'sd1659;
            7'd112: zeta_lookup = -16'sd1185;
            7'd113: zeta_lookup = -16'sd1530;
            7'd114: zeta_lookup = -16'sd1278;
            7'd115: zeta_lookup =  16'sd794;
            7'd116: zeta_lookup = -16'sd1510;
            7'd117: zeta_lookup = -16'sd854;
            7'd118: zeta_lookup = -16'sd870;
            7'd119: zeta_lookup =  16'sd478;
            7'd120: zeta_lookup = -16'sd108;
            7'd121: zeta_lookup = -16'sd308;
            7'd122: zeta_lookup =  16'sd996;
            7'd123: zeta_lookup =  16'sd991;
            7'd124: zeta_lookup =  16'sd958;
            7'd125: zeta_lookup = -16'sd1460;
            7'd126: zeta_lookup =  16'sd1522;
            7'd127: zeta_lookup =  16'sd1628;
        endcase
    end
endfunction

function automatic logic signed [15:0] barrett_reduce16(input logic signed [15:0] x);
    logic signed [31:0] mult;
    logic signed [31:0] addv;
    logic signed [31:0] t;
    logic signed [31:0] r;
    begin
        mult = 32'sd20159 * x;
        addv = mult + 32'sd33554432;
        t    = addv >>> 26;
        r    = x - t * 32'sd3329;
        barrett_reduce16 = r[15:0];
    end
endfunction

function automatic logic signed [15:0] montgomery_reduce16(input logic signed [31:0] t);
    logic signed [47:0] tqinv;
    logic [15:0]        u16;
    logic signed [31:0] uq;
    logic signed [31:0] r;
    logic signed [31:0] shifted;
    begin
        tqinv = t * 32'sd62209;
        u16   = tqinv[15:0];
        uq    = $signed(u16) * 32'sd3329;
        r     = t - uq;
        shifted = r >>> 16;
        montgomery_reduce16 = shifted[15:0];
    end
endfunction

always_ff @(posedge clk) begin
    if (rst) begin
        busy  <= 1'b0;
        out_a <= 16'h0000;
        out_b <= 16'h0000;
    end else if (input_accepted) begin
        logic signed [15:0] a_s;
        logic signed [15:0] b_s;
        logic signed [15:0] zeta_s;
        logic signed [15:0] sum_raw;
        logic signed [15:0] diff_raw;
        logic signed [31:0] prod_raw;

        a_s      = $signed(in_a);
        b_s      = $signed(in_b);
        zeta_s   = zeta_lookup(in_twiddle_index[6:0]);
        sum_raw  = a_s + b_s;
        diff_raw = b_s - a_s;
        prod_raw = zeta_s * diff_raw;

        out_a <= barrett_reduce16(sum_raw);
        out_b <= montgomery_reduce16(prod_raw);
        busy  <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
